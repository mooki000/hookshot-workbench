# AI: NPCs and enemies

How the actors' AI runs each frame: the AI controller units that run an actor's `.ainb` graphs and the code that ticks
their nodes, the actor update slots that drive them, the NPCs' brain-and-action component (BSA) and schedules
(AISchedule: rails, chasing, standing, talking), perception and sensors, and what limits how often all this runs. It
was looked at in November 2026 by static analysis of the binary, for the frame-rate work: [jobs.md](jobs.md) found the
frame bound by the game's logic, and [hot-path.md](hot-path.md) found AI and NPC code among the hottest functions.
[sequences.md](sequences.md) has the node classes, their registry and vtable; this page builds on it.

**Longshot code:** none yet
**See also:** [sequences.md](sequences.md) (node classes, vtable slots, parameter binding), [actors.md](actors.md) (the
actor class, its five jobs, LODParam and calc skip), [animation.md](animation.md) (slot 51, the LOD module, calc
skipping by whole frames), [jobs.md](jobs.md) (the thread pools, AIGroupParallelCalc), [physics.md](physics.md) (Havok
AI: navigation, collision avoidance, the async sensor queries), [hot-path.md](hot-path.md) (the hot ranks),
[player.md](player.md), [frame-time.md](frame-time.md) (the frame step and the frame-time keeper)

## In short

1. **The per-frame driver of a graph is the actor's own update.** An actor's AI component (components `+0x48`) keeps a
   list of AI controller units and four indices into it (`+0x10`, `+0x14`, `+0x18`, `+0x1c`). Each frame the actor's
   update slots run one unit each: slot 51 (job 0) through the BSA update `f_800eb9c0` runs unit `[+0x10]`, slot 52
   (job 1) unit `[+0x14]`, the player's movement update `f_82c415e0` unit `[+0x18]` and slot 53 (job 2) unit `[+0x1c]`.
   Each call is `f_800e7780(unit, step, scaled step)`, which ticks the graph's root nodes through `f_800e98c0` (hot rank
   15); container nodes tick their children through it again. This answers [sequences.md](sequences.md)'s open
   question on who calls Enter and Update. **read**
2. **A node's Update gets the frame step.** Slot 51 reads the frame-time keeper's step and scaled step (`+16`, `+24`)
   and hands them down; the unit keeps them at `+0x1a0` and `+0x1a4`, and the argument a node's OnUpdate gets is a
   context pointer and the two floats. So nodes that add the step are frame-rate independent. **read** (slot 51's path),
   **guess** (that every caller passes the keeper's values)
3. **NPCs and enemies are BSA plus AISchedule.** BSA (Brain, action slots, utility graphs; `game__component__BSAParam`)
   is the decision layer, updated in slot 51 (`f_800eb9c0`, rank 47). AISchedule (`game__component__AIScheduleParam`)
   is the NPCs' daily life: schedules, rail walking (`IsWaitingAtRailPoint`), chasing (`game__npc__NpcChase`),
   postures (`Standing`, `Sit`, `Crouch`), talk and terror. Its update `f_802d57c0` (rank 348) runs from the BSA update,
   and its movement `f_80406b80` (rank 278) also runs from a parallel list job (`f_80161870`). **read** (the calls),
   **guess** (the roles, from the strings)
4. **Perception is a module plus a per-actor receiver.** The "Perception" module's calc (`f_80d465e0`, rank 456) and
   each actor's receiver update (`f_800f8910`, rank 397, from slot 51) do sight, hearing, terror, intuition and sensor
   checks. The Logic node `HoldActorObserverBySensorHitInfo`, in 859 of the 1,822 loose Logic files, runs its 1,504
   instructions every Update (`f_804afb80`, rank 238). **read**
5. **Many AI and actor jobs run every N frames, counted in frames.** Besides the calc skip ([animation.md](animation.md)),
   slot 51 runs one component every 60 frames and another every 30 or 150, slot 52 one every 60, two other actor updates
   one every 10, each staggered by the actor's BaseProc ID; the AI groups run every N frames. Above 60 fps these run
   more often in real time (60 frames is 0.42 s at 144 fps). **read** (the tests), **guess** (what each gated call does)

## Functions

### Running a graph each frame

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_800e7780` | `AIControllerUnit_Update(unit, s0 step, s1 scaled)` | Hot rank 128 (796 instructions). Keeps the two steps at unit `+0x1a0` and `+0x1a4`, zeroes the tick count of its context (`[unit+0x38]+8`), ticks the root nodes with `f_800e98c0` (at 0x800e78b8 and 0x800e793c) and runs `f_8076d7b0` and `f_800df9c0`. Its actor is unit `+0x1f0`. Called from GameActor slots 52 and 53, the BSA update, the player's movement update (`f_82c415e0`, [player.md](player.md)), the AI component's slot 6 (`f_800b5400`), the actor base's slots 60, 61 and 63, and `f_83b31f10` (beside the EventMember component's code) | read |
| `f_800e98c0` | `AINode_Tick(node, ctx)` | Hot rank 15 (2,082 instructions): ticks one node. Returns at once when bit 0 of node `+0x1f` is set; counts the tick (`[ctx]+8`); calls the node's Update (slot 8), and when slot 8 is the shared `Node_Update` (`f_837cbb70`, [sequences.md](sequences.md)) calls slot 30 itself; then checks the result bits and flags at `+0x18`. It has 51 call sites, most of them the container nodes' updates at 0x800dd000–0x800ef000, so a graph is ticked by recursion | read |
| `f_800e9800` | `Element_Simultaneous_OnUpdate(node, ctx)` | Hot rank 70: slot 30 of `Element_Simultaneous` (vtable `0x84c492b8`). Reads its children from its parameter block in the file (count at `+0x94`, first at `+0x95`, table at `+0xa4`), looks each up in the graph's node array (`[node+8]+0x68`, count `+0x60`) and ticks every one with `f_800e98c0`, each Update | read |
| `f_800f24a0` | `Element_Sequential_OnUpdate` | Hot rank 1531: slot 30 of `Element_Sequential` (vtable `0x84c49188`); reads the same child table | read |
| `f_8076cce0` | `Element_BoolSelector_OnUpdate` | Hot rank 1841: slot 30 of `Element_BoolSelector` (vtable `0x84c48720`) | read |
| `f_800fd280` | `Element_StringSelector_OnUpdate` | Hot rank 948: slot 30 of `Element_StringSelector` (vtable `0x84c49648`) | read |
| `f_800e63c0` | `Element_ModuleCaller_OnUpdate` | Hot rank 144 (1,208 instructions, the string `%s.ain`): slot 30 of `Element_ModuleCaller` (vtable `0x84c48c98`), the node that runs another graph (a `.module` reference) inside this one | read |
| `f_800b5400` | `AIComponent_vt6` | Hot rank 101 (1,464 instructions): slot 6 of the engine class with vtable `0x84c7ae20` and of the game's subclass `0x84b457f8`. Calls `AIControllerUnit_Update` (at 0x800b5f58) unless a lookup by the hash 0x99814c0d (`f_80d8d4d0`) returns a set flag. **guess**: the AI component's own update | read |
| `f_83aecbb0` | `LogicUnit_TickRoots(unit, ctx, owner)` | Slot 9 of the engine vtable `0x84c7aff8` (whose slot 4 names `Logic`, `CommonLogic`, `CommonNode`): ticks each root node (24-byte entries at `[[owner+0x20]]+0x30`, count `+0x28`) with `f_800e98c0` | read |
| `f_800e94c0` | `GameLogicUnit_TickRoots` | Hot rank 163: the game's override of that slot 9 (vtable block `0x84b458b0`, inside the game class `0x84b457f8`); calls `f_800e98c0` | read |
| `f_83adb520` | `AIModule_Calc` | Slot 2 (`+16`, the module calc) of the AIModule vtable `0x84c7a7c8` ([sequences.md](sequences.md)): adds 1 to the AI system's tick count (`[0x84ea52a8]+0x38`), skipping 0 when it wraps. Nothing else | read |
| `f_83b02580` | `AIGroupParallelCalc_Run` | The "AIGroupParallelCalc" job ([jobs.md](jobs.md)): each thread that runs it claims the next AI group by an atomic add on `+0x34` (up to `+0x38`) and calls the group's slot 4 (`+0x20`) with the two steps (`+0x90`, `+0x94`). When the byte at `+0x98` (N) is not 0, a group whose `+8` minus the frame (`[0x84eb0d90]+0x440`, plus 1) is not a multiple of N is called with a skip flag set. Not in the hot list | read |

### The BSA: brain, actions and utility

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_800eb9c0` | `BSA_Update(actor, &steps)` | Hot rank 47 (4,760 instructions; `OriginalActionVerbArg`, `AdjustedActionVerbArg`, `cReservedDefaultFormChangeKey`, `%s.ain`). Called from GameActor slot 51 (at 0x82c3e33c, row in [animation.md](animation.md)) with the frame-time keeper's step and scaled step. Reads the BSA component (components `+0x188`) and the perception receiver (`+0x330`), Nest (`+0x308`), Ridable (`+0x3a0`) and Liftable (`+0x2c0`) components; calls the AISchedule update `f_802d57c0` (at 0x800ec820) and others for components `+0x2e8`, `+0x418`, `+0x3d8`, `+0x2a0`; runs the AI component's unit `[+0x10]` (at 0x800ec478) and its own graphs (`f_800e7780` three times, `f_800e98c0` four, `f_8076d7b0` four) | read |
| `f_82d3f3b0` | `BSAParam_Visit` | `game__component__BSAParam` (name `f_82d40b20`): BlackboardTableRef, BrainAinPath, BrainVerbAttributesSettingPath, BrainCalcOptions, SchedulerParamPath, ActionSlotMainAin, ActionSlotSubAin, ActionSlotSPAin, ActionSlotSP2Ain, ActionSlotSP3Ain, ActionVerbAttributesSettingPath, UtilityAinPath, NoCalcSkipUtilityAin, PostSensorUtilityAinPath, ModelOnlyLODMode, EyeLidControllerParam, IsMOLDisableDungeonRoomID. Its twin `f_82d3fff0` names the same fields | read |
| `f_82cbf060` | `BrainCalcOptions_Visit` | `game__bsa__BrainCalcOptions` (name `f_82cbf640`; default file `Work/BSA/BrainCalcOptions/Default.game__bsa__BrainCalcOptions.gyml`): IsCalcDuringInterrupt, IsAllowCalcSkipForLOD | read |
| `f_82dea2f0` | `BSA_CreateControllers` | A slot of the vtable word `0x84b67af8` (strings `Brain,Action,Utility`, `cReservedDefaultBrainVerbAttributesKey`, `cReservedDefaultActionVerbAttributesKey`): makes AI controller units through `f_83ad8e90` ([sequences.md](sequences.md)), at 0x82dea6d8 | guess |
| `f_80417ba0` | `SelectorNpcBSAInterruptAction_Calc` | Hot rank 316 (3,236 instructions): the calc (slot 39) of `SelectorNpcBSAInterruptAction`, which picks an NPC's interrupting reaction (`DontBSAInterruptAction`, `IsReceiveSpreadTerror`, `game__npc__NpcReactionParam`, `NpcGoronRolling`, `Arrow`, `Shootable_Hook`) | read |

### NPC schedules (AISchedule)

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_802d57c0` | `AISchedule_Update(sched, &steps)` | Hot rank 348 (6,516 instructions; `game__aischedule__AIScheduleSpecialParam`, `game__npc__InterestReceiveParam`, `game__npc__NpcSpecialParam`, `IgnoreAIScheduleChangeAttentionValidity`, `FaceEmotion`, `Sleepy`, `IsFinishKnockDownTimer`). Called from the BSA update on the AISchedule component (components `+0x138`); calls `f_80404bc0` and `f_8040d8a0` | read |
| `f_80404bc0` | `AISchedule_IsNeedCalcInLOD(sched, flag)` | Hot rank 274 (1,964 instructions; `CoupleNpc`, `NpcMusician`): the query `QueryAIScheduleIsNeedCalcInLOD`'s calc (`f_803ff060`) is this call with flag 0, its result the query's output. Looks a parameter up by the hash 0xb6f7e643 in a table of the actor's. Also called from the AISchedule update and movement, `f_802c9ca0` and `f_8278943c` | read |
| `f_8040d8a0` | `AISchedule_UpdateRail` | Hot rank 497 (1,476 instructions; `IsWaitingAtRailPoint`, `CurDamageCount`, `CurTerrorCount`). From the AISchedule update, its movement (at 0x804078f8) and `f_80ae7110` (slot 22 of the AISchedule component's vtable) | guess |
| `f_80161870` | `AIScheduleList_Worker(job)` | Hot rank 484: takes entries off a shared list one at a time by compare-and-swap (the cursor at job `+0xc8`), so several threads can run it at once, and for each whose owner (`[entry+0x18]`) has bit 15 of `+0x30` set and `+0x2c` equal to 5 calls `f_82dd3cf0` with the job's bytes `+0xd0` and `+0xd1` | read |
| `f_82dd3cf0` | `AISchedule_ParallelCalc(entry, a, b)` | Hot rank 369, in the AISchedule component's code: checks a value (hash 0x0aba75d6) of the actor's component `+0x118`, then tail-calls `f_80406b80` on `entry+0x208` | read |
| `f_80406b80` | `AIScheduleMove_Update(move, a, b)` | Hot rank 278 (about 6,100 instructions; `game__npc__NpcChase`, `IsWaitingAtRailPoint`, `Standing`, `Walk`, `Run`, `Dash`, `Door_Open`, `Sit`, `Crouch`, `SitOnObject`, `IsInChaseArea`, `Animal_Dog`, `game__animal__KokkoParam`, `NpcSkullFamily`). Reads the frame step and scaled step and hands them to the model update `f_800d43d0` (at 0x80409cc4); calls `f_80404bc0` and `f_8040d8a0`. **guess**: an NPC's walking, chasing and posture | read |
| `f_80b255b0` | `AIScheduleList_Register(mgr)` | Sets up the list job at `mgr+0x90` (vtable `0x84b31a00`, run `f_80161870` at `+0xa0`, `+0xb0` = 2) and links `mgr+0x38` into the list at `[[0x84ca23a0]]+0x978` under the mutex `+0x998`. Called from `f_832d3030` (a vtable slot that also names `MinimumField.bcett.byml`) | read |
| `f_82d2f240` | `AIScheduleParam_Visit` | `game__component__AIScheduleParam` (name `f_82d2fa20`): BlackboardTableRef, IsSimplifyActor, AIScheduleLodDistance (`+0x78`), IsInvalidAIScheduleLOD (`+0x7c`). Its twin `f_82d2f660` | read |
| `f_828bb630` | `NPCCommonUtilityModule_OnUpdate` | Hot rank 797 (2,008 instructions): slot 30 of the compiled module `NPCCommonUtilityModule`; reads `IsCancelPauseAIScheduleLOD`, `IsPlayerChild`, `game__npc__NpcSpecialParam` | read |
| `f_808b2000` | `NpcFairyModule_OnUpdate` | Hot rank 223 (3,228 instructions): slot 30 of the compiled module `NpcFairyModule` (`LookAtPlayerOnEventEnter`, `FairyMoveTarget`). **guess**: Navi | read |

### Perception and sensors

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80d465e0` | `PerceptionModule_Calc` | Hot rank 456 (596 instructions): the "Perception" module's calc (`+16` of its vtable `0x84bbb398`; the module is 0xcf0 bytes). Works under mutexes and wakes waiters (`__cxx_atomic_notify_all`) | read |
| `f_83226420` | `PerceptionModule_Init` | Slot 7 of that vtable: makes the "PerceptionHeap" and loads `game__perception__TerrorFilterTable_Master` | read |
| `f_800f8910` | `PerceptionReceiver_Update` | Hot rank 397 (1,228 instructions): called first thing in GameActor slot 51 on the component at components `+0x330` when its `+0x34` bit 0 is set and `+0x3a` is 0. That component is the one the `OneShotPerception*` and `QueryPerception*` nodes use (SenseScale, SightParam, IntuitionRange) | read |
| `f_82d8c780` | `PerceptionEmitterParam_Visit` | `game__component__PerceptionEmitterParam`: BlackboardTableRef, SubLayerSensor, IsResidentEmitter, NoiseAndTerrorOnlyEmitter, IsCreateStaticRigidBody, NoEmitUntilConditionsMet, AdvancedEmitters | read |
| `f_82d8d210` | `PerceptionSenses_Visit` | The senses: SightParam, HearingParam, TerrorParam, IntuitionParam and SensorParam (`game__perception__SightParam` and the others); `f_82d8d650` adds the Custom… overrides | read |
| `f_82d8db50` | `PerceptionReceiverParam_Visit` | `game__component__PerceptionReceiverParam`: DefaultFilter, IsEnabledByDefault, IsReceiveFromSensorWorld, IsReceiveResidentEmitter, DisableIfInsideEnemyForbidArea, NoReceiveEmitInEnemyForbidArea, IsAffiliationOptimizeDisable, IsForceNormalCalcMethod, IsForceSystemRange, ForceSystemRange | read |
| `f_804afb80` | `HoldActorObserverBySensorHitInfo_Update` | Hot rank 238 (1,504 instructions): slot 40 (each Update) of the Logic nodes `HoldActorObserverBySensorHitInfo` (859 loose Logic files) and `HoldActorObserverWithInstanceNameBySensorHitInfo` (26) | read |

### Other per-frame costs beside the AI

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_800d43d0` | `ModelComponent_Update(model, &steps)` | Hot rank 27 (6,484 instructions, 101 indirect calls, 86 guarded type objects). Called from GameActor slot 54 (`f_800cdbe0`, [actors.md](actors.md), twice) and from `AIScheduleMove_Update`, each with the step pair; reads the step itself too. Its object is the component at components `+0x260`, its actor at `+0x18`; it clears and tests the flags at `+0x120` and `+0x130`, averages a float (`+0xb4`) over a list, and multiplies five factors (`+0x78`, `+0x7c`, `+0x80`, `+0x118`, `+0xa0`) into a value for the component at `+0x2f0` (`f_82e7b1b0`); a callee names `%s_EyeOuter`. **guess**: the model's per-frame update (alpha, visibility, eyes), because `OneShotModelSetAlpha` writes `+0x78` and a flag in `+0x120` | read |
| `f_82ef62f0` | `EnvAnimalSwarm_Update` | Hot rank 31 (3,208 instructions; `sinf`, `fmodf`): slot 6 of the vtable `0x84b747e0` beside the class names `EnvAnimalBase` and `EnvAnimalKokiriInsect`. **guess**: the Kokiri Forest insect swarms, each insect on an orbit | guess |
| `f_82ef9510` | `EnvAnimalSwarmParam_Visit` | Slot 7 of the same vtable: IsMovementOrbital, IsSpawnNearCamera, IsOrbitPlayer, BoneNum, WavePerRandomize, BoxHeight/Width/Depth, InnerDiameter, AppearHeightFromGroundMin/Max, ModelScale, SphereRadius, MoveSpeed, Amplitude, Period, ShapeCastRadius, NearFadeMax/MinDistance, DefaultAlpha, WeakenedState… | read |
| `f_803d8750` | `Actor_Periodic150` | Hot rank 778: called from GameActor slot 51 every 150 frames (every 30 when bit 9 of the component's `+0xa8` is set), staggered by the actor's BaseProc ID, for the component at components `+0x460`; reads three floats at `+0x31c` (**guess**: the actor's position) and calls `f_803d71c0` (rank 446) | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84ea52a8` | `g_AISystem` | The AI system's holder, referenced from 1,233 places; `+0x38` is the tick count `AIModule_Calc` advances | read |
| `0x84c7aff8` | `LogicUnit_vtable` | The engine's Logic graph controller (slot 4 the categories `Logic`, `CommonLogic`, `CommonNode`; slot 9 `f_83aecbb0`), set by `f_83aec950` | read |
| `0x84c7ae20` | `AIComponent_vtable` | An engine class whose slot 6 is `f_800b5400` and slot 7 (`f_83ae13c0`) makes the Logic controller. **guess**: the AI component | guess |
| `0x84b457f8` | `GameAIComponent_vtable` | The game's subclass of it (slot 6 `f_800b5400`), with the Logic controller's slots in the same block from `0x84b458b0` | guess |
| `0x84b66390` | `AIScheduleComponent_vtable` | The AISchedule component (its icon getter, slot 37, names `ResourceIcon\AISchedul…`; slot 19 `f_82dd18c0` reads the NPC parameters) | guess |
| `0x84b31a00` | `AIScheduleListJob_vtable` | The list job's functor vtable (`f_80b255b0`) | read |
| `0x84bbb398` | `PerceptionModule_vtable` | The "Perception" module (calc `f_80d465e0`, slot 7 `f_83226420`) | read |
| `0x84b747e0` | `EnvAnimalSwarm_vtable` | Slots 6 and 7 `f_82ef62f0`, `f_82ef9510` | guess |
| `0x84c492b8` | `Element_Simultaneous_vtable` | Slot 30 `f_800e9800`, slot 29 `f_8076d660`, slot 31 `f_8017d8f0` | read |
| `0x84c49188` | `Element_Sequential_vtable` | Slot 30 `f_800f24a0` | read |
| `0x84c48720` | `Element_BoolSelector_vtable` | Slot 29 `f_8076bd40` (shared with the StringSelector), slot 30 `f_8076cce0` | read |
| `0x84c49648` | `Element_StringSelector_vtable` | Slot 30 `f_800fd280` | read |
| `0x84c48c98` | `Element_ModuleCaller_vtable` | Slot 30 `f_800e63c0` | read |
| `0x84c48878` | `Element_Expression_vtable` | Slots 29 and 30 `f_837d12c0`, `f_837d12d0`; slot 39 `f_837d1520` | read |
| `0x84ca40d0` | `got_ActorModule` | The GOT slot holding the Actor module (`0x84eb1920`, [jobs.md](jobs.md)), through which the actor jobs read the frame number at `+0x1c4` | read |
| `0x83ce4a7c` | `str_AIainbPath` | `AI/%s.ainb`; with `Logic/%s.ainb` (`0x83df09d0`) used by `f_803fbc80` and `f_8071f0a0`, the actor resource path builders [actors.md](actors.md) names for AS | read |

## Structures

### The actor's components as the AI uses them (`[actor+0x270]`)

Named by the node classes whose calcs read each slot (counted over the 5,716 classes' slots 29, 30, 39 and 40), and by
the code on this page. **read** (the offsets and users), **guess** (the names)

| Offset | Field | Sure |
|---|---|---|
| `+0x48` | The AI component (**guess**: AIInfo): the controller units (below) | read |
| `+0x118` | Logic (`QueryLogicGetBool`, `ExecuteLogicSet*`) | guess |
| `+0x138` | AISchedule (the `*AISchedule*` nodes; the AISchedule update) | read |
| `+0x188` | BSA (`ExecuteBSAInvokeActionVerb*`, `ExecuteBSASetActionVerb`, `ExecuteBSARequestUpdateBrainNextFrameDelegate`; the BSA update) | read |
| `+0x1f8` | Equipment (`QueryActorIsEquipment`, `QueryEquipmentIsEquiped`); slot 51 calls its `+0x100` every 60 frames | guess |
| `+0x260` | Model (`OneShotModelSetAlpha`, `OneShotSetModelVisible`, `OneShotShopItemSetModelLodManual`; `f_800d43d0`) | guess |
| `+0x2c0` | Liftable (`QueryLiftableIs*`) | guess |
| `+0x308` | Nest, the enemy's territory (`QueryNestIsOutsideOfTerritory`, `QueryEnemyGetHomeOrNestPositionAndRotation`) | guess |
| `+0x330` | The perception receiver (`OneShotPerceptionChangeSenseScale`, `QueryPerceptionGetSenseSightParam`, `EnemyCommmonPerceptionTargetSearchAndSetModule`) | read |
| `+0x3a0` | Ridable (`QueryRidableIs*`, `SelectorBSAHorseWanderAI`) | guess |
| `+0x460` | Read by `ExecuteWindmill`, `QueryIsInHidingHole`, `QueryAIScheduleGetRescueWarpAnchor`; slot 51 calls `f_803d8750` on it every 30 or 150 frames (**guess**: WorldInfo) | read |

### The AI component's units (components `+0x48`)

| Offset | Field | Sure |
|---|---|---|
| `+0x10` | The unit run by the BSA update in slot 51 (job 0) and by the base's slot 60 | read |
| `+0x14` | The unit run by slot 52 (job 1) and the base's slot 61 | read |
| `+0x18` | The unit run by the player's movement update (`f_82c415e0`) and the base's slot 63 | read |
| `+0x1c` | The unit run by slot 53 (job 2) | read |
| `+0x28` | The list: `+0` the count, `+8` the entries; an entry's `+0x18` is the unit `f_800e7780` updates | read |

### An AI controller unit (as `f_800e7780` uses it)

| Offset | Field | Sure |
|---|---|---|
| `+0x38` | Its tick context; `+8` counts the nodes ticked this update (zeroed by `f_800e7780`, counted by `f_800e98c0`) | read |
| `+0x1a0` | The frame step for this update | read |
| `+0x1a4` | The scaled step; the OnUpdate argument's second float | read |
| `+0x1f0` | The actor | read |

A node's graph instance (`[node+8]`, [sequences.md](sequences.md)) has its unit at `+8` (so a node finds its actor at
`[[node+8]+8]+0x1f0`), the file holder at `+0x10`, and the node pointers at `+0x68` (count `+0x60`). **read**

### The Actor module's frame numbers (`[0x84ca40d0]` = `0x84eb1920`)

| Offset | Field | Sure |
|---|---|---|
| `+0x1c0` | The previous frame number | read |
| `+0x1c4` | The frame number, copied each module calc (at 0x83aff69c) from `+0x440` of the object `[0x84eb5f48]` holds, which `f_801c5ef0` adds 1 to from the Actor module's calc | read |

## Notes

### One actor's AI in a frame

1. Job 0 runs GameActor slot 51 (`f_82c3d740`, [animation.md](animation.md)). It reads the frame-time keeper's step and
   scaled step into a pair, updates the perception receiver (`f_800f8910`), the model and many other components, then
   calls the BSA update `f_800eb9c0(actor, &pair)`. That updates the AISchedule (`f_802d57c0`) and other components,
   runs the AI component's unit `[+0x10]` and BSA's brain and action graphs. **read**
2. Job 1 runs slot 52 (`f_82c3fab0`, [actors.md](actors.md)): unit `[+0x14]`. Job 2 runs slot 53 (`f_82c43780`): unit
   `[+0x1c]`. Link's movement update runs unit `[+0x18]`. **read**
3. Each unit's update ticks its roots; `Element_Simultaneous` ticks all its children every Update, the selectors and
   `Element_Sequential` their current one, and `Element_ModuleCaller` a whole sub-graph. A node is ticked through
   `f_800e98c0`, which counts it and calls its OnUpdate (slot 30) directly when the class keeps the shared Update. **read**
   (Simultaneous, the tick), **guess** (what the others tick)
4. Off the actor's jobs: the AISchedule list job (`f_80161870`) runs each listed NPC's movement on whatever threads take
   it; the "AIGroupParallelCalc" job runs the AI groups; the Perception module's calc runs as a module ([jobs.md](jobs.md),
   graph A). Where the AISchedule list job is started from was not found. **read** (the functions), **guess** (where
   they run)

Which unit is which BaseProc phase (PreCalc, PostCalc, PostSensor, PostUpdateMatrix) is not proven: BSAParam has a
`PostSensorUtilityAinPath`, and the four indices fit the four phases. **guess**

### How often the AI runs

- **Whole actors:** an actor's jobs are pushed or skipped by its LOD and calc-skip settings ([actors.md](actors.md)), and
  the LOD module's worker sets the jobs' calc-skip interval, counted in whole frames ([animation.md](animation.md)). An
  actor whose jobs are not run runs none of its AI. **read** (on those pages)
- **The brain:** `game__bsa__BrainCalcOptions` has `IsAllowCalcSkipForLOD` and `IsCalcDuringInterrupt`, BSAParam has
  `NoCalcSkipUtilityAin` and `ModelOnlyLODMode`, and many enemies have a node that asks for the brain on the next frame
  (`OneShotRequestUpdateBrainNextFrame<Enemy>`: Stalfos, Lizalfos, Dodongo, Iron Knuckle, Keese, the Poe sisters, …;
  `ExecuteBSARequestUpdateBrainNextFrameDelegate`). So the brain is not always worked out every frame. **guess** (from
  the names; the gate in `f_800eb9c0` was not found)
- **NPC schedules:** AIScheduleParam has `AIScheduleLodDistance` (`+0x78`) and `IsInvalidAIScheduleLOD` (`+0x7c`); the
  NPC parameters have `IgnoreNpcWhenAIScheduleLOD`, `IsAlwaysChaseWhenAIScheduleLOD` and `IsCancelPauseAIScheduleLOD`;
  `AISchedule_IsNeedCalcInLOD` (`f_80404bc0`, rank 274) decides, and the graphs can ask it through
  `QueryAIScheduleIsNeedCalcInLOD`. **read** (the fields and the query), **guess** (that the distance is from the camera
  or player)
- **AI groups:** the group job calls a group with a skip flag except every Nth frame (N the byte at `+0x98`), staggered
  by the group's `+8`. **read**

### Counted in frames

Code that tests the frame number (`[0x84ca40d0]+0x1c4`) against the actor's BaseProc ID (actor `+0x18`) modulo a
constant, so each actor does the work on its own frame of N: **read**

| Where | Every | What runs |
|---|---|---|
| Slot 51, at 0x82c3d7f0 | 60 frames | The equipment component's `+0x100` (components `+0x1f8`) |
| Slot 51, at 0x82c3d844 | 150 frames, or 30 | `f_803d8750` on components `+0x460` |
| Slot 52, at 0x82c40828 | 60 frames (or when actor `+0x762` bit 6 is set) | `f_82c426e0` |
| `f_82c42960` (rank 250), at 0x82c42e14 | 10 frames (or when actor `+0x6e8` bit 5 is set) | Work on components `+0x460` |
| Slot 56 (`f_800c34e0`, [actors.md](actors.md)), at 0x800c40c0 | 10 frames (the same bit) | A branch of slot 56 |
| `AIGroupParallelCalc_Run` | N frames (`+0x98`) | The AI groups |

At 60 fps 60 frames is a second; at 144 fps it is 0.42 s, so these run 2.4 times as often in real time. Whether any of
them adds a fixed time per call (which would then run fast) was not read. Node timers are a different case: the frame
step reaches the nodes (*In short*, 2), and `SeqExecuteGenericWaitFrame` adds the step ([sequences.md](sequences.md)).
The nodes whose names count frames (`ExecuteAIScheduleIsOverCurrentPointWaitFrame`, `SelectorGenericFrameCounter`,
`ExecuteGenericCounter`, `QueryAIScheduleGetTakeOffHeightAndFrame`, the parameters `AttackIntervalFrames`,
`MoveIntervalFrame`, `FallIntervalFrame`, `IntervalFrame`) were not checked one by one. The worklist's jump animation
that keeps playing in dialogue above 60 ([../worklist.md](../worklist.md), #1742) is the kind of bug such a counter
would cause; nothing here ties it to one. **guess**

### What costs

From the hot ranks ([hot-path.md](hot-path.md)), not measured per scene: the node tick (`f_800e98c0`, 15), the model
update reached from the NPCs' movement (`f_800d43d0`, 27), an ambient swarm (`f_82ef62f0`, 31), the BSA update (47),
`Element_Simultaneous` (70), the AI component's update (101), the unit update (128), `Element_ModuleCaller` (144), the
Logic roots (163), Navi's module (223), the sensor-hit observer (238), the NPC schedule code (274, 278, 348, 369, 484,
497), the perception receiver (397) and module (456). Havok AI's navigation and collision avoidance, the longest piece of
the frame's critical path, is on [physics.md](physics.md) and [jobs.md](jobs.md). The AI's cost scales with the number
of actors whose jobs run and with the size of their graphs: every Update of a container ticks its children again.
**guess**

### Enemies

Enemies are the same machinery: a BSA with brain verbs (`BrainVerbOnEnemyDiscovered`, `BrainVerbOnInvestigate`,
`Brain_LoseSightOfTarget`, `Brain_DontUpdateIfSameTarget`, `UpdateBrainIfDiscoverPlayer`), action slots, perception
(`EnemyCommmonPerceptionTargetSearchAndSetModule`), a territory (the Nest component, `EnemyCommonSelectorCheckTerritory…`),
and each enemy's own parameters (141 `game__enemy__*` classes, from `game__enemy__EnemyCommonParam` and
`EnemyGlobalParam` to the bosses'). The sensor queries go through the physics world's async queries
(`AsyncQueryAabb(Sensor)`, [physics.md](physics.md)). **read** (the names), **guess** (the roles)

## Open questions

- What starts the AISchedule list job (`[[0x84ca23a0]]+0x978`) and on which pool it runs, and what its entries' owner
  fields (`+0x2c` = 5, `+0x30` bit 15) mean.
- Who calls the AI component's slot 6 (`f_800b5400`) and the base slots 60, 61 and 63, and whether the four unit indices
  are the four BaseProc phases.
- Whether `[0x84eb5f48]` and `[0x84eb0d90]` hold the same object (both have a frame count at `+0x440`).
- Where the BSA update decides not to rework the brain (`IsAllowCalcSkipForLOD`, the "update brain next frame"
  requests), and what the AISchedule LOD distance is measured from.
- What `f_80406b80`'s `entry+0x208` object is, and whether `f_800d43d0` is the Model component's update (the alpha
  fields fit).
- Which AI nodes count updates instead of time; a scan of the AI classes' OnUpdate for `+1` counters would list them.
- What the AI system's tick (`[0x84ea52a8]+0x38`) is used for: only four functions were found reading `+0x38` near the
  holder.
