# Animation, skeletons and the LOD module

How an actor's animation and skeleton are driven each frame, and the engine module that decides how often each actor is
updated at all. The game plays its animations through the **AS** component (the animation sequencer: `AS/<name>.asb`
files, with `Model/<name>.anim.bfres` for the clips), turns the sequencer's events into bone controllers (helper and
support bones, cloth, look, hand and foot IK) in the actors' own update, and has a **LOD** module whose parallel worker
is the ninth-hottest function in the profiles. Looked at in November 2026 by static analysis of the binary, for the
frame-rate work: which parts are hot, what they cost per bone or per actor, and which of them count frames rather than
time.

**LS code:** `host/oot_coop_players.c` (named-node requests and frame setting, current-frame sampling).
The step these parts read is the one `patch_frame_seconds` sets (`nx2/oot_patches.py`, [frame-time.md](frame-time.md)).
**See also:** [actors.md](actors.md) (the five jobs, the LOD and calc-skip settings), [frame-time.md](frame-time.md) (the
step and the frame-time keeper), [jobs.md](jobs.md), [culling.md](culling.md) (the sphere queries),
[hot-path.md](hot-path.md) (the hot ranks), [maps.md](maps.md) (the ActorInfo row's animation fields)

## In short

1. **The hottest function on this page is not animation but the LOD module's worker** (`f_80021ca0`, hot rank 9, 7,340
   instructions). The module named "LOD" (vtable `0x84c84ac0`) hands it to five helper jobs through a delegate and runs
   it on its own thread too; each copy pops actors' LOD entries off a shared lock-free stack. Per entry it reads the
   actor's main rigid body (checking `mLastTransform` and `mPrevTransform` for NaN), fills and reads back the actor's
   occlusion sphere query (`nvnMemoryPoolGetFlags` and an invalidate before reading), measures distance and angle
   (`sqrt`, `acos`), accumulates the actor's time, and sets the **calc-skip interval** of the actor's jobs. Its work
   scales with **actors**, not bones. **read** (the code), **guess** (that the strings it shares with the physics
   getters make the transforms a rigid body's)
2. **Calc skipping counts whole frames.** A job's functor holds an interval (`+0x14`) and a phase (`+0x15`, the low byte
   of the actor's BaseProc ID); the LOD worker treats the job as due only when `(frame − phase) mod interval` is 0, the frame being
   the BaseProc manager's counter at `+0x440`. The module's calc staggers other work by `(ID − frame) mod N` and
   `mod 5`. Above 60 fps these run more often in real time (an interval of 2 is 30 Hz at 60 fps and 72 Hz at 144).
   Whether a skipped job gets a larger step when it does run was not found. **read** (the tests), **guess** (what is
   gated)
3. **Animation time follows the frame step.** The AS module's calc (`f_83ade290`) advances a global animation frame
   counter by the frame's step, carrying the fraction (`[0x84ea5718]+0x48`, `+0x50`); a sequencer slot's current frame is
   a float (`+0x10c`, read by `QueryASGetCurrentFrame`); and actor job 0 installs the actor's **time group** (slot 57)
   in its core's frame-time slot before slot 51 runs, scaling the step by that group's rate. **read**
4. **The skeleton's controllers run in the actors' own update**, not in a module: GameActor slot 51 (`f_82c3d740`, hot
   rank 14) and slot 52 (`f_82c3fab0`, rank 12, [actors.md](actors.md)) call the bone-controller update `f_8018dba0`
   (rank 104), which reads the sequencer's named events (`LookIK`, `HandIK`, `FootIK`, `FloorContact`, `HelperBone`,
   `SupportBone`, `Cloth`, `FixedChain`, `BoneModifier`, `RagdollReaction`, …) and switches the controllers. The IK and
   bone maths beneath it (`f_80341840`, `f_80337ae0`, `f_801d99a0`, `f_802b9900`) is float-heavy and string-less. **read**
   (the calls), **guess** (the roles below it)
5. **Not found:** where an animation clip is sampled and blended, and where the bones' local transforms become world
   matrices. The model's calc (`f_8009bb80`, rank 8, [hot-path.md](hot-path.md)) reads the skeleton's world matrices (64
   bytes a bone) to rebuild each shape's bounding sphere (`f_800a8400`, rank 32), so that work scales with shapes times
   bone spheres.

## Functions

### The LOD module

The module's vtable is `0x84c84ac0` (slot 2 the calc, slot 7 the set-up, slots 25 and 26 the name). Like the other
modules ([jobs.md](jobs.md)), its calc is run by its module calc job (`f_83b78d80`) with the frame's argument: `+0` the
frame's seconds and `+4` the step, both from the current core's frame-time slot (`f_80706be0`, [frame-time.md](frame-time.md)).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83b785a0` | `LODModule_Name` | Slot 25 (and 26 the same): returns "LOD" (`0x83dbd0eb`) | read |
| `f_80b246e0` | `LODModule_Setup(mod, info)` | Slot 7: keeps an object from `info+0x30` at `+0x950`, fills the entry lists (`f_80b247f0`), sets the lists' node offsets (`+0x44` to 0x20, `+0x7c`, `+0x94`, `+0xac` to 0x30), the helper count `+0x8c0` to 5, bit 2 of `+0x9f0`, and the delegate at `+0x840`: vtable `0x84c84c20`, object the module, method `f_80021ca0` | read |
| `f_83b74d30` | `LODModule_Calc(mod, frame)` | Slot 2 (hot rank 139, 2,824 instructions): under the mutexes at `+0x908` and `+0x998` runs the listeners at `+0x960`; checks the frame's flag set for the key 0x9f0b75e7 (as the AS calc does for its own), builds view volumes (`sinf`, `cosf`, `tanf`; **guess**: one per camera), staggers per-entry flags by the frame number (0x1000 in the entry's `+0xc4` when `(BaseProc ID − frame) mod N` is not 0, 0x400 when `mod 5` is not 0), starts the helper jobs (`f_80428800`, with the count `+0x8c0`: **guess**) and runs `f_80021ca0` on its own thread (at 0x83b7614c) | read |
| `f_83b78880` | `LODWorkerDelegate_Invoke` | The delegate's invoke (vtable `0x84c84c20`, slot 0): calls the method on its object, going straight to `f_80021ca0` when that is the method | read |
| `f_80021ca0` | `LODModule_Worker(mod)` | Hot rank 9, 7,340 instructions. Adds one to `+0x8d0` and pops entries off the lock-free stack at `+0x8c8` (compare-and-swap) until the list's end. Per entry (the actor at `+0x10`): reads its main rigid body's transforms (`f_80158940`, `f_80158c10`, each NaN-checked, the error 剛体[%s]にNan(%s)が指定されました, "NaN set on rigid body [%s] (%s)", with `mLastTransform` or `mPrevTransform`); fills a sphere query (position at `+0x80`, radius at `+0x8c`) and steps its states (`+0x24`), reading results after `nvnMemoryPoolGetFlags` (6 calls) and an invalidate; distances and angles (`sqrt` ×8, `acos`); adds step × `[keeper+0x1c0]` × the actor's group rate to `+0x98`; sets the 64-bit state at `+0xb8` and writes the interval byte of jobs 0 to 3 (`functor+0x14`: 1, or 2 for job 3) and tests `(frame − phase) mod interval` for each (from 0x80024f18). Work: per actor entry, with mutexes (9 locks) around shared parts | read |

### The AS module and the animation resources

The AS module's vtable is `0x84c7ab88` (slot 2 the calc, slot 7 the set-up, slot 9 the heap, slots 25 and 26 the name).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83ade3f0` | `ASModule_Name` | Slot 25 (and 26, `f_83ade400`, the same): returns "AS" (`0x83cf6991`) | read |
| `f_83ade290` | `ASModule_Calc(mod, frame)` | Slot 2 (68 instructions; not in the profile's list): unless the frame's flag set holds the key 0xe87cdf6a, adds the frame's step (`frame+4`) to the AS system's frame counter: the whole part to the 64-bit `+0x48`, the fraction to `+0x50`, carrying it. So the counter keeps time at any frame rate | read |
| `f_80f93200` | `ASModule_Setup` | Slot 7 (140 instructions): makes the AS system (`f_80f93440`) and its parts | read |
| `f_80f93440` | `ASSystem_Create(heap)` | When `0x84ea5718` is empty: allocates 0x300 bytes, makes its disposer at `+0x2e0` (kept at `0x84ea56f8`), builds it (`f_80f934c0`) and stores it in `0x84ea5718` | read |
| `f_80fd36e0` | `ASModule_MakeHeap` | Slot 9: asks for a heap named "ASModule" for the module (`f_80fd3e10`) | read |
| `f_83b17cf0` | `AnimationComponent_Bind` | The Animation component's resources (976 instructions): `Model/%s.anim.bfres`, the events `AnimationEvent/Animation/*.baev`, `Model/%s.%s.bfres` and RetargetScale | guess |
| `f_809727c0` | `ModelResources_Paths` | Builds an actor's model paths: `Model/%s.%s.bfres`, `Model/%s.anim.bfres` from its ModelProjectName, and the PreActorRenderInfo | guess |
| `f_8299a830` | `QueryASGetCurrentFrame_Evaluate` | The script query's slot 39: from the node's actor (`+0x1f0` of its owner), the components (`actor+0x270`), the AS reference (`+0x40`, then `+0x18` twice); for the slot index input below the slot count (`AS+0x18`), takes slot `[AS+0x20] + 0x48 × i`, whose `+0x10c` is the current frame (a float); outputs it and frame ÷ the clip's length (its node's vtable `+0x40`, at least 1) | read |

### Actor requests, duration and frame setting

These rows retain **read** from the supported-build static investigation of the accessors and request routine.
`host/oot_coop_players.c` at `2f01e1b4bdb6e1629c54a998f0403519fe4e5c74` calls the request and setter
from `anim_play`; its `AS_SLOT_FRAME` comment and `anim_read` distinguish the current frame from duration.
`f_8299a830` above reads the two values separately.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_837d57c0` | `ASControl_GetDuration` | Validates the slot and active node, then dispatches its duration getter; unavailable returns zero. This is not the slot's current playback frame (`AS_SLOT_FRAME`) | read |
| `f_802222a0` | `Actor_RequestASNode` | Takes an actor, slot and locally named animation-node request, applies rate/start/blend options and returns whether the native request succeeds; already-playing can also succeed. Success alone does not establish visible bone output (`anim_play`) | read |
| `f_837d5d70` | `ASControl_SetFrame` | Resolves the slot's active animation node and dispatches a frame-setting operation with the supplied float; absent/out-of-range nodes return without action (`anim_play`) | read |

### The components that carry animation

Makers from the engine's component registry (`f_81209b40`, row in [actors.md](actors.md)), each listed under its
ActorParam name. Each makes the small reference object an actor holds to the component; what the component does is
from its name.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80b9cd90` | `ASInfoRef_Make` | "ASInfoRef": the AS settings (ASInfoAutoPlaySkeletals, …Materials, …BoneVisibilities in the ActorInfo row, [maps.md](maps.md)) | guess |
| `f_80968e30` | `ASOptimize_Make` | "ASOptimize" | guess |
| `f_80fb0d20` | `AnimationRef_Make` | "AnimationRef": the clips (`Model/<name>.anim.bfres`) | guess |
| `f_80973640` | `RetargetScaleRef_Make` | "RetargetScaleRef" | guess |
| `f_808bf3b0` | `ASRef_Make` | "ASRef": the sequencer; its reference is at `components+0x40` (`f_8299a830`) | guess |
| `f_808fd020` | `ModelBindRef_Make` | "ModelBindRef" | guess |
| `f_80c6dc00` | `BoneBindRef_Make` | "BoneBindRef" | guess |
| `f_83af0180` | `LODRef_Make` | "LODRef": the actor's LOD component, the LOD module's entries (**guess**) | guess |
| `f_808aaf80` | `CalcSkipRef_Make` | "CalcSkipRef" | guess |

### The actor's update: bones, IK and the controllers

Job 0 (`f_800b73d0`, [actors.md](actors.md)) calls slot 50, then slot 51; slot 52 is job 1's.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_82c3bbb0` | `GameActor_Calc50` | GameActor's slot 50 (hot rank 62, 1,712 instructions), run by job 0 before its time group is installed; calls `f_83b873c0` | read |
| `f_82c3d740` | `GameActor_Calc51` | GameActor's slot 51 (hot rank 14, 1,992 instructions, the string "CommandUseIK"), run by job 0 in the actor's time group: the bone-controller update `f_8018dba0`, the rotation maths `f_802b9900`, a rigid body's motion (`f_8023c820`, `mLastTransform`), the AI command update (`f_800eb9c0`) and many of the player's state updates | read |
| `f_83b873c0` | `BoneModifier_Calc` | From slot 50 and the base's slot 51 (rank 75, 556 instructions); beside the functions naming DisableBoneModifier, BoneModifier, BoneModifierPostIK | guess |
| `f_80194010` | `BoneControl_Apply` | From `f_8018dba0` (rank 530, 1,580 instructions): calls the IK driver `f_80337360` and `f_80195aa0` | guess |
| `f_80337ae0` | `IK_Drive` | From `f_80337360`, which `f_80194010`, `f_8030c240` (the path from slot 52) and others call (rank 160, 7,888 instructions, 2,064 float operations); calls the solver `f_80341840` | guess |
| `f_80341840` | `IK_Solve` | Rank 98, 4,880 instructions of which 2,776 are float operations, no indirect calls, mostly maths (`sinf` ×13, `acosf` ×12, `sqrtf`, `sincosf`, `atan2f`) and a few helpers (`f_8031ef80`, `f_8031c540`): a chain solver | guess |
| `f_80195aa0` | `BoneControl_Distances` | Rank 169 (1,096 instructions, `sqrtf`), from `f_80194010` and `f_8030c240` | guess |
| `f_801d99a0` | `BoneChain_Simulate` | Rank 76, 14,412 instructions (5,505 float operations, 174 square roots, 70 `sqrtf` calls, `atan2f`, `acosf`), the string UseRotatedTranslate; from `f_801d7ac0`, beside the functions naming `Tail_%d`, `Head`, `HeadToCursorBone`, `meshBone`, `noClothSimulation` and `Skl_Root`: **guess**, a swinging bone chain (tails, hair) | guess |
| `f_802b9900` | `Bone_RotationMaths` | Rank 97 (3,472 instructions: `sqrtf` ×24, `sinf`, `acosf`, `atanf`), from slots 51 and 52 and `f_802ca820` | guess |
| `f_8003dac0` | `Bone_Maths2` | Rank 43 (2,463 instructions, `atan2f`), from `f_8003d320` and `f_80320ae0`, under the bone-controller path | guess |
| `f_800a8400` | `Shape_BoundsFromBones(shape, skel)` | Rank 32: rebuilds a shape's bounding sphere from its bone spheres. For each sphere (count at `[shape]+0x58`; bone index from a 16-bit list) moves its centre by the bone's world matrix (`[skel+0x20]`, 64 bytes a bone), scales its radius by the matrix's largest axis (three square roots), and merges it into the enclosing sphere (1e-4 slack). Called per shape by the model's calc `f_8009bb80` (rank 8) and from three other places | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84c84ac0` | `LODModule_vtable` | The LOD module | read |
| `0x84c84c20` | `LODWorkerDelegate_vtable` | The delegate that runs `f_80021ca0` (invoke `f_83b78880`) | read |
| `0x84c7ab88` | `ASModule_vtable` | The AS module | read |
| `0x84ea5718` | `g_ASSystem` | The AS system (0x300 bytes, `f_80f93440`): `+0x48` the animation frame counter (64-bit), `+0x50` its fraction, `+0xe0` a mutex's pointer, `+0x30` an object several updates call | read |
| `0x84ea56f8` | `g_ASSystemDisposer` | The AS system's disposer (`+0x2e0` of it) | read |
| `0x83cb0005` | `str_ASPath` | `AS/%s.asb` | read |
| `0x83d15b99` | `str_AnimBfresPath` | `Model/%s.anim.bfres` | read |

## Structures

### The LOD module (vtable `0x84c84ac0`)

| Offset | Field | Sure |
|---|---|---|
| `+0x38`, `+0x44` | The entry list and the offset of an entry's node (0x20) | read |
| `+0x840` | The worker delegate (vtable, object `+0x848`, method `+0x850`) | read |
| `+0x8c0` | How many helper jobs run the worker (5) | read |
| `+0x8c8` | The lock-free stack of entries still to do | read |
| `+0x8d0` | Workers that have started, counted atomically | read |
| `+0x908`, `+0x998` | Two mutexes the calc takes | read |
| `+0x960` | A list of listeners the calc calls (their `+0x28` object's slot 0) | read |
| `+0x9f0` | Flags; bit 2 set by the set-up | read |

### A LOD entry (one an actor)

| Offset | Field | Sure |
|---|---|---|
| `+0x10` | The actor | read |
| `+0x20` | The entry's list node | read |
| `+0x40`, `+0x48`, `+0x50`, `+0x60` | Pointers to the actor's parts the worker reads (**guess**: model, physics, AS and calc-skip components) | read |
| `+0x80` | −1 when unset (an index) | read |
| `+0x90` | A 16-bit index, 0xffff when unset | read |
| `+0x92` | A byte counted up once a pass, to at most 11 (resetting `+0xb0`) | read |
| `+0x98` | Time accumulated: step × the global rate × the actor's group rate | read |
| `+0x9c` | Flags changed atomically | read |
| `+0xb4`, `+0xb5` | Two bytes (a level and its previous value: **guess**) | read |
| `+0xb8` | The 64-bit state the worker computes | read |
| `+0xc4` | 16-bit flags the calc sets (0x400, 0x1000, 0x2000) | read |
| `+0xc8` | Which of jobs 0 to 3 the interval applies to (bits 0–3) | read |

### A sequencer (AS) and its slots

| Offset | Field | Sure |
|---|---|---|
| actor components `+0x40` | The AS reference; its `+0x18`, then that object's `+0x18`, is the sequencer | read |
| AS `+0x18` | The number of slots | read |
| AS `+0x20` | The slots, 0x48 bytes each, the slot object first | read |
| slot object `+0x10c` | The current frame, a float | read |
| slot object `+0x48` | The node that knows the clip's length (its `+0x50`, vtable `+0x40`) | read |

## Notes

### Who runs what, each frame

- **Stage 5's BaseProc phase** ([jobs.md](jobs.md)) runs each actor's jobs. Job 0 calls slot 50 (`f_82c3bbb0`), then
  installs the actor's time group and calls slot 51 (`f_82c3d740`); job 1 calls slot 52 (`f_82c3fab0`). Slots 51 and 52
  both reach the bone controllers (`f_8018dba0`) and the rotation maths (`f_802b9900`); the IK below them runs inside
  the same job, on the same thread. **read**
- **The LOD module** runs as a module calc in the module phase: one copy of its worker on the module's thread and five
  more as jobs, all draining one stack. Which stage its calc job sits in was not traced. **read**
- **The AS module's calc** is tiny. The sequencer's per-actor work (advancing each slot's frame, firing its events) runs
  elsewhere and was not found: no function that writes a slot's `+0x10c` stood out among the hot ones.
- **The model's calc** (`f_8009bb80`, rank 8, under the render slot work `f_800998a0`) is the drawing side's
  ([hot-path.md](hot-path.md)); among other work it rebuilds each shape's bounds from the skeleton's world matrices
  (`f_800a8400`), from the shape list at model `+0x158` and its 0x70-byte records at `+0x100`, the skeleton at `+0xf8`.
  **read**

### Time and frames

- **The time groups.** Job 0 calls the actor's slot 57 (`GameActor_vt57`, [actors.md](actors.md): `actor+0x5f8`), a
  group index below 16. On an executor thread it then fills that core's slot of the frame-time keeper
  (`[[0x84c9e890]]+0x20 + 0x30 × core`): `+0` the step × `[keeper+0x1c0]` × `[keeper+0x140 + 8 × group]`, `+4` its
  inverse, `+8` it ÷ 60 (seconds), `+12` the inverse × 60 (fps); pushes the group on that slot's small stack (`+0x10`,
  `+0x18`, `+0x1c`, `+0x20`) and sets the core's nibble in `[keeper+0x1c4]`. The step pair handed to slot 51 is scaled by
  the group's rate over group 0's, and kept at `actor+0x2e8`. So code inside slot 51 that reads the keeper's per-core
  slot sees the actor's own time. **read**
- **Fine above 60:** the AS frame counter (fraction carried), a slot's float frame, the LOD entry's time (`+0x98`).
  **read**
- **Counted in frames:** the calc-skip interval of each job (`(frame − phase) mod interval`, with the BaseProc manager's
  frame number at `[0x84eb0d90]+0x440`, plus one), the LOD calc's staggering (`mod N`, `mod 5`), and the LOD entry's
  byte at `+0x92` (one a pass, to 11). They are sites for [frame-time.md](frame-time.md)'s list: an actor on interval
  2 is updated at half the frame rate, whatever that is. Whether its step is then the time since its last update (which
  would keep its speed) or one frame's step (which would halve it, at any frame rate) depends on how the job's argument
  (`functor+0x18`) is filled when it is pushed, which was not found. **read** (the tests), **guess** (the effect)

### What scales with what

- Per actor: the LOD worker's entry (`f_80021ca0`), slots 50 to 52, the bone controllers (`f_8018dba0`), which read the
  event parameters by name (a count at `+8`, an element's value at `+8` of it). **read**
- Per shape times its bone spheres: `f_800a8400`. **read**
- Per bone: not shown for any function here. The IK solver (`f_80341840`) and the chain function (`f_801d99a0`) are
  the most float-heavy; their loops were not read.

### Resource paths

`f_803fbc80` (and its twin `f_8071f0a0`, reached through the GOT slot `0x84caa908`) is a general resource path builder,
not an animation one: one function knows `AS/%s.asb`, `AI/%s.ainb`, `Logic/%s.ainb`, `.gyml`, `.byml`, `Banc/` and more,
with about 140 callers. The AS component's resource binder (`f_803fb0c0`, slot of `0x84c7a9f0`, the string
"ASComponent") loads the sequencer's events (`AnimationEvent/AsNode/*.baev`) through it. **read**

## Open questions

- Where a clip is sampled and blended, and where the bones' world matrices are computed (in the model's calc, in the AS
  component's update, or in a library whose strings were stripped). Havok Animation's types are in the binary
  (`HK_JOB_TYPE_ANIMATION_SAMPLE_AND_BLEND`, the compressed animation kinds), but no hot function names them.
- Where the sequencer advances its slots' frames (`+0x10c`) and by what: the step, the step × ASRate, or whole frames.
- How the job argument at `functor+0x18` is filled when a job is pushed, and so whether a calc-skipped actor gets the
  time since its last update.
- Who increments the BaseProc manager's frame number at `+0x440`.
- What the LOD worker's 64-bit state at `+0xb8` means bit by bit, and how it maps to the LODParam fields
  (`ForbidPushJob*`, `EnableCalcSkip`, `CalcIntervalOnCalcSkipForce`, [actors.md](actors.md)).
- What the LOD calc's view volumes are (one per camera, or the main camera and a shadow view), and what `N` is in its
  `mod N` staggering.
- What `f_82c3bbb0` (slot 50) does besides the bone modifiers, and whether `f_8003dac0`, `f_802b9900`, `f_80341840`,
  `f_801d99a0` loop over bones.
- A live check of the hot ranks per scene: how many LOD entries a frame has, and how many of them are skipped.
