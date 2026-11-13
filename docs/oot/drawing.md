# Drawing: the CPU's side of the frame's GPU work

What the game does on the CPU to turn its scene into NVN commands: who drives the draw each frame, which threads record
command buffers and how they are submitted, how models become draw items and are split into batches, and what one
draw costs (material, uniform, texture and state binds, and the draw call). The frame rate is CPU-bound, and this is the
part of the game's CPU time nothing else documents. Looked at in November 2026 by static analysis of the binary and of
LS's code (no builds, no runs). Hot ranks are positions in `hot_order.txt` ([hot-path.md](hot-path.md)).

**LS code:** `host/nvn_native.c` (every NVN call below lands in a handler there:
[nvn-calls.md](nvn-calls.md)), `gpu/vk/vk_replay.cpp` (the replay a submit starts),
`host/hos_thread.c` (`YieldThread`, which the recording threads call after each batch)
**See also:** [nvn-calls.md](nvn-calls.md) (the NVN functions and what each costs under LS), [jobs.md](jobs.md)
(the threads and the stage slot jobs), [culling.md](culling.md) (the occlusion queries and conditional draws inside the
draw loops), [rendering.md](rendering.md) (the passes on the GPU), [hot-path.md](hot-path.md) (the ranks)

## In short

1. **The draws are recorded in parallel, in many small command buffers.** Each pass's draw list is cut into chunks
   (`f_80554440`), and each chunk becomes a 40-byte batch. The ModuleSystem's stage slot jobs (`f_80083960`, on the five
   `ModuleSystemWorker`s and the main thread, [jobs.md](jobs.md)) take batches one at a time with an atomic counter and
   record each into its own NVN command buffer (`f_80084de0`, rank 16). The same jobs also take larger "draw tasks"
   (`f_8006cd60`, rank 28; `f_8007b520`; `f_8007d2e0`), each a list of render steps (`f_8006e160`, rank 24). **read**
2. **Every batch starts from nothing.** The worker builds a fresh draw context for each batch. So the batch's first
   draw binds everything again: render state, program, uniform blocks, textures. A batch also costs about 20 fixed NVN
   calls: `Initialize`, `AddControlMemory`, `AddCommandMemory`, `BeginRecording`, the memory callbacks, `SetRecordingMode`,
   timestamps, viewport, scissor, depth range, and then `EndRecording`, two memory-used queries and `Finalize`. The
   worker yields its thread after every batch. **read**
3. **Within a batch, binds are cached.** The material bind (`f_8008fc00`, rank 35) keeps the ids of what it bound last
   in the draw context: the program, each uniform block, the texture set, the render-state class. It issues an NVN call
   only when one differs. A draw that shares its material with the one before costs one to three calls:
   `CallCommands` for a shape with commands recorded earlier, `BufferGetAddress` and `DrawElementsInstanced` for each
   indexed submesh, or `DrawMeshTasks`. A full render-state change costs about 35 calls (`f_80077d60`). **read**
4. **Models never bind vertex buffers.** The model draw path (`f_80078fc0`, `f_8008fc00`) calls no `BindVertexBuffer` or
   `BindVertexAttribState`. Geometry reaches the shaders through storage buffers and the index buffer's address. The
   vertex-buffer binds are in the post effects, the shadows and the debug draws. **read**
5. **GPU-driven drawing still costs a CPU draw per shape.** A shape that uses `gsys_geometry_culling` is drawn with one
   `DrawElementsIndirect` per view, whose counts the GPU writes (`f_83607e80`). The material bind before it is the same
   as for any other shape. The meshlet shapes' task shaders cull on the GPU ([culling.md](culling.md)), but the CPU still
   issues a `DrawMeshTasks` per shape. **read** `f_83607e80` is not in `hot_order.txt`, so few shapes take this path in
   the profiled play. **guess**

## The frame, from the top

On the main thread the framework runs the calc tree, then the draw tree (`f_802eb020`, [jobs.md](jobs.md)). The draw
tree reaches the system task's TV draw, `Draw(TV)` (`f_8066fc80`, through the delegate `f_8066fb40`). That function
builds the frame's main draw context, opens the frame's first command buffer (`f_80665980`), and draws the main screen
(`Draw(drawMainScreen_)`, `f_802ab6c0`). `PostDraw(TV)` (`f_836ce750`) runs afterwards and flushes the queued submits
through `f_83560df0` → `f_834bf400` → `f_80527f00`. **read**

The scene's passes are recorded by `f_80660320` (rank 41). It calls 62 game functions, among them the allocators of
[rendering.md](rendering.md)'s render targets. It opens four more command buffers (`f_806650d0`) and submits twice
(`f_806668b0`). Between them it signals and waits on NVN events (`SignalEvent` ×9, `WaitEvent` ×10), so the frame's GPU
work runs as several command buffers on more than one queue. `f_80660320` is reached through `f_806602e0`, which
`f_80eeb4b0` registers in a table of handlers keyed by 32-bit hashes. **read** (the calls), **guess** (that it is the
scene's whole pass list)

The `Layer(Draw)`, `Layer(SubDraw)` and `Layer(GPUCalc)` labels are only set up in `f_80f8f5b0`, which copies them under
a lock. `DrawPass/DrawPass` is a parameter that the effects' custom renderers read (`f_808807d0`, `f_80881980`,
`f_81290e30`). `UserPass%d` and `UserQueue%d` are names in the draw lists' debug dump (`f_835c1380`). None of these was
followed further. **read**

### Who records, and when

The stage slot jobs ([jobs.md](jobs.md): six per stage, run by `f_80083960`) carry a 32-bit work mask at `job+0x10`.
`f_836d0880` sets it when it builds the frame context's jobs: 0x8, 0x400, 0x40/0xc0, 0x3000/0x7000, 0x3, 0x100,
0x20/0x30, 0x200, 0x4, and 0x10000/0x20000 (the stage's begin and end, which run nothing). **read** Each set bit makes
the job run one kind of work, most of them shared between the six slots by an atomic counter:

| Bit | What the slot runs | Sure |
|---|---|---|
| 3 | `f_800998a0` (rank 11; [jobs.md](jobs.md)'s `ModuleJob_RunWithPoll`), which calls `f_8009bb80` (rank 8): GPU buffers | read |
| 5 | `f_800738e0` (rank 133) | read |
| 6 | `f_8007e200` (rank 135) | read |
| 8 | `f_800766e0` (rank 328), once per scene record | read |
| 10 | **The draw-list batches**: for each scene record in the list at `+0x38` of the object in `0x84ea2780` (count at `+0x30`), take batch *i* by an atomic add on `rec+0x3c84`, stop at `rec+0x3c28`. For each batch, build a draw context (`f_80021a10`) and record the batch with `f_80084de0`. Then, when the context still holds an open slot, close it with `f_8007de00`, and call `YieldThread` | read |
| 12 | `f_8007c120` (rank 1446), once per scene record | read |
| 13 | The draw tasks again, through `f_80083580` | read |
| 15 | **The draw tasks**: take a generic job (`+0x4dc` over `+0x4e8`, array `+0x4f0`, each run through its vtable `+0x10`), then the draw tasks (`+0x4e0` over `+0x4f8`, array `+0x500`), in the object at `+0xd0` of the frame context holder's (`0x84ea2de0`, [jobs.md](jobs.md)). A task's type at `+0x10` picks the recorder: 0 `f_8006cd60`, 1 `f_8007d2e0`, 3 `f_8007b520` | read |
| 0, 1, 2 | Other work (`f_8007a490`, `f_8007d0d0`, `f_8007a8f0`; `f_80672260`, `f_80072960`; `f_80764040`, `f_80763da0`, the effect manager's: [effects.md](effects.md)), not followed | read |

So all six participants of pool B record draws at once, each into its own command buffer. That is why the recording
functions are on [jobs.md](jobs.md)'s critical path: `f_8006cd60` is 13.5% of it, `f_800998a0` 8%. **read** (the
dispatch; the shares were measured on [jobs.md](jobs.md))

### Command buffers and submits

A command buffer comes from a pool of 152-byte slots, at `+0x278` of the graphics object in `0x84e9fca8` (the count at
`+0xd0`, the array at `+0xd8`), indexed by a frame number times `+0xea` plus the slot. A recorder fills the slot and
hands it to `f_8007de00`. That function ends the recording, keeps the handle and the memory used in the slot, and then either submits it at once
(`submitNow`: queues 3 and 5 in the bit-10 path) or appends it to its queue's pending list. The queues are 96 bytes each
in the same graphics object: queue *i*'s NVN queue at `+0x30 + i*0x60`, its mutex at `+0x38`, and two pending lists (count,
capacity, list; one per frame, by the byte at `+0x2a8`) from `+0x40`. `f_80527f00` ([nvn-calls.md](nvn-calls.md))
puts the pending command buffers in their recorded order and submits them in one `QueueSubmitCommands`, then `QueueFlush`. Its callers are `PostDraw(TV)` and
`f_80527e90`, the game's own `QueueSubmitCommands`, which first flushes what is pending. **read**

Under LS a submit replays its command buffers on the submitting thread ([nvn-calls.md](nvn-calls.md)). So the
command buffers that the bit-10 path submits at once are replayed on whichever worker recorded them, and the rest on
the thread that flushes (the main thread, from `PostDraw(TV)`). **read** (the code paths), **guess** (how often each runs)

## Draw lists and batches

### From draw types to batches

`f_80553cd0` calls `f_80554440` 30 times, once for each draw type (the `Model(...)` names: `Model(ZPrePass)`,
`Model(Opa+AlphaMask)`, `Model(Xlu)`, `Model(OpaWater)`, `Model(ReducedBuffer/Xlu)`, `Model(DeferredShading)`,
`Model(Reflection/Opa+AlphaMask)`, …). For one type, `f_80554440`:

1. Fills a pass descriptor on the stack: the draw type's name, a mask, flags and a cost factor (`+0xbc`). It then
   copies it, 192 bytes, into the view at `view+0x3830 + type*0xd0`.
2. Skips the type when the draw list's mask (`list+0x14`) misses the view's, or when the list is empty (`list+0x2c`).
3. **Cuts the list into chunks.** The number of chunks is the item count divided by `rec+0x3c38` (the smallest chunk),
   capped at `rec+0x3c3c`. Each chunk becomes a 40-byte batch, appended at `rec+0x3c18` by an atomic add on `rec+0x3c20`,
   with its pointer added to `rec+0x3c30` (count `+0x3c28`, capacity `+0x3c2c`). A batch holds the view, the draw list, the
   pass descriptor, the chunk's first item and count, and a weight: the count scaled by the type's factor (half, a
   tenth or ten times, by `+0xbc`). **read**

What the weight is for (ordering the batches, or balancing them) was not found. The two limits are run-time values.
**guess**

### Sorting

The draw lists' debug dump (`f_835c1380`) prints each shape's sort key as `ModelSortKey : 0x%016llx (Runtime:%d
PriorityHint:%d Priority:%d)`. The key's top 16 bits are the shape's priority word (`shape+0x22`): Runtime in bits 10 to
13, PriorityHint in bits 6 to 9, Priority in bits 0 to 5, each stored with a bias of 8, 8 and 32. Under them, from bit 20,
is a value from the shape's vtable `+0x60` (a material or shader id: **guess**), and from bit 12 the byte at `+0x20`. So
draws sort by priority first, then by that id, which keeps a material's draws together. The sort itself was not found,
and neither was where the lists are filled. **read** (the key's layout), **guess** (that this is the order drawn)

### The draw loops

`f_80084de0` (rank 16; [jobs.md](jobs.md)'s `ModuleJob_Dispatch`) records one batch:

- It hashes the batch's 0x70-byte pass key with CRC32 and looks it up, under the list's mutex (`list+0x48`), in the
  list's cache of 224-byte entries (`list+0x30`/`+0x38`, count `+0x40`), comparing the whole key.
- It takes a command-buffer slot (an atomic add on the context's `+0xb8`), then calls `Initialize`, `AddControlMemory`,
  `AddCommandMemory`, `BeginRecording`, the memory callbacks, `SetRecordingMode` and two timestamp `ReportCounter`s. It
  sets the viewport, scissor, depth range, line width and point size.
- It builds an eight-bit loop mode from the pass's flags and calls one of 256 loop variants from the table at
  `0x84c29878` with (items, count, context, view mask, flags, delegate). Two modes are inlined in `f_80084de0` itself:
  0x80, the query loop of [culling.md](culling.md) (`f_835cd630`'s kind), with the shape draw devirtualised to
  `f_80078fc0` when the item's vtable `+0x58` is it, and 0x40.
- It ends with a timestamp, `EndRecording`, the two memory-used queries, `Finalize`, and the handle stored under a lock.
  **read**

The table at `0x84c29878` holds 256 loop variants, indexed by the mode. Entries 16 to 63 set conditions (the 258
template copies of [culling.md](culling.md)'s `f_835c5460`), 64 to 79 and 128 to 139 run queries, 80 to 127 do both,
from 140 on a mix of the two, and the first 16 do neither. All but 13 are copies at 0x835c3e90–0x83604000 that no
profile ranks. The hot ones are entry 5 (`f_8008b3e0`, rank 412: no queries, the shape draw inlined), entry 160 (`f_8008d640`, rank 108: queries and
conditions, the shape draw inlined), entry 32 (`f_80078ce0`, rank 509) and entry 132 (`f_80078490`, rank 1460).
[culling.md](culling.md)'s generic loop `f_80522a20` is entry 176: it has no direct callers, only this table. Besides
`f_80084de0`, only `f_80681320` (rank 357) reads the table. It draws a list into the command buffer already open, for the
passes that draw a list inline (`f_80255be0`, `f_806688c0`, `f_8028a2a0`, `f_806811d0`). **read**

Every loop variant does the same per item. It skips the item unless its view mask (`item+8`) meets the pass's. When
the item's render-state class (`item+0xe`, bits 0 to 3) differs from the one applied, it applies it (`f_80077d60`).
When bit 4 of `item+0xe` changes, it resets the context (`f_80078b10`). Then it calls the item's shape draw: vtable `+0x58` of
`item+0x10`, with the LOD (`item+0xe` bits 6 to 9) and the flags. The loops also take a per-item filter delegate, a
member-function pointer compared with a no-op first. **read**

## Per draw

### The shape draw

A drawable shape has one of several vtables that share a base (slots `+0x10` to `+0x48`). Slot `+0x10` is `f_80511fd0`,
which sets the shape up and gives it a geometry-culling object at `+0x80` when it uses `gsys_geometry_culling`. Slot
`+0x48` is `f_80521a70`, which allocates the shape's `gsys::ShapeDL` memory. Slot `+0x58` is the draw:

- `0x84c2a130`: `f_80078fc0` (rank 426), the common one.
- `0x84c324d0`: `f_800955a0` (rank 69): the same, with its own query and condition handling and more uniform binds.
- 252 more functions at 0x8366d820–0x836a8da0, each in a vtable at 0x84c30000–0x84c32fff, also call `f_8008fc00`: 252
  of its 263 callers, none of them ranked. **guess**: more drawable classes' draws. **read** (the rest)

`f_80078fc0`, for one shape and LOD:

1. Returns at once when the LOD is past the shape's count (`shape+0x1e`, bits 4 to 7).
2. Sets the geometry processing mode when the shape's (`[shape+0x68]+0x69e`) differs from the context's (`ctx+0x3b`).
3. Binds the material (`f_8008fc00`). If that fails, nothing is drawn.
4. **Draws.** When the LOD's entry (`[shape+0x68]+0x638`, 624 bytes per LOD) has recorded commands (bit 0 of `+0x58`) and
   the pass allows them (`ctx+0x5c` bit 5), it calls `CallCommands` once with the handle at `+0x50`. Otherwise it draws
   each submesh with `f_8002a5f0` (the index buffer's address) and `f_8008f840` (`DrawElementsInstanced`), or calls
   `DrawMeshTasks`. A shape with a geometry-culling object calls `f_83607e80` instead. Other branches handle instanced and
   per-view variants (`f_83607730`, `f_83607840`, `f_8029fef0`).
5. Adds the LOD's draw count (`+0x268`) to the context's statistics (`ctx+0x50`). **read**

### The material bind

`f_8008fc00` (rank 35) binds what the shape's material needs and keeps the ids in the draw context:

| What | When it binds | NVN calls | Sure |
|---|---|---|---|
| Render state | The shape's state byte (`shape+0x58`) differs from the one applied (`ctx+0x59`; derived bytes at `+0x155`, `+0x15b`, `+0x15c`) | The whole state block, as in `f_80077d60` | read |
| Program | The material's program (`mat+8`) is not the one bound (`ctx+0xd8`) | `BindProgram` | read |
| Uniform blocks | A block's 32-bit id (`ctx+0x78` … `+0x94`) differs | One `BindUniformBuffer` per stage the block is used in (a packed word with a slot byte per stage, `0xff` for none) | read |
| Storage buffers | The same | `BindStorageBuffer` per stage | read |
| Textures | The texture set's id (`ctx+0x8c`) differs | For each of up to 30 slots in the program's mask: `GetTextureHandle`, then `BindTexture` per stage, or one `BindTexturesDeferred` | read |
| Recorded binds | The material has a recorded command set | `CallCommands` | read |

So the per-draw cost depends on the order of the draws: draws that share a material cost only the draw call. Whether
the per-model matrices are one of the uniform blocks (and so re-bound for every model) was not settled. **guess** (that
they are)

### What one draw costs in NVN calls

Read from the code above. Each NVN call is a host call under LS ([nvn-calls.md](nvn-calls.md)).

| Case | NVN calls | Sure |
|---|---|---|
| Same material, shape with recorded commands | 1 (`CallCommands`) | read |
| Same material, indexed shape | 2 per submesh (`BufferGetAddress`, `DrawElementsInstanced`) | read |
| Same material, meshlet shape | 1 (`DrawMeshTasks`), plus `SetGeometryProcessingMode` when the mode changes | read |
| Geometry-culled shape | 3 per view (`SetPrimitiveRestart`, `DrawElementsIndirect`, `SetPrimitiveRestart`) | read |
| New program | +1 (`BindProgram`), and the blocks it uses re-bound: one call per block per stage | read |
| New texture set | +2 per texture and stage (`GetTextureHandle`, `BindTexture`), or 1 (`BindTexturesDeferred`) | read |
| New render state | about 35 (`f_80077d60`): blend (5 per colour target), colour (4), channel mask (9), depth-stencil (7), polygon (6), clamps, stencil values (3) | read |
| A z-prepass query around it | +3 (`ResetCounter`, `BufferGetAddress`, `ReportCounter`; [culling.md](culling.md)) | read |
| A conditional draw | +1 or 2 (`SetRenderEnable`, `SetRenderEnableConditional`) | read |
| Each batch | about 20 fixed (above), and the first draw binds everything | read |

## Indirect and GPU-driven paths

- **Geometry culling** (`GeometryCulling(ZPrePass)` and `(GBuffer)`, [culling.md](culling.md)): a shape opts in at set-up
  (`f_80511fd0`). For each view the CPU still binds the material and issues `DrawElementsIndirect` (`f_83607e80`;
  `f_83607f60` on [nvn-calls.md](nvn-calls.md) is the plain copy). The GPU only decides the counts, at `+0x14` of the
  view's 32-byte entry. **read**
- **Meshlets**: `DrawMeshTasks` from the shape draw and the inlined loops (`f_80078fc0`, `f_8008b3e0`, `f_8008d640`,
  `f_80084de0`), and `DrawMeshTasksIndirect` from 14 functions with no strings, each a drawable's vtable slot (the hottest
  `f_801a46a0`, rank 1896, at `0x84b41fe0`; `f_82c03470`, rank 2045, at `0x84b42490`, which also sets the tiled cache).
  **read** (the calls), **guess** (that these are the grass, foliage and effects)
- **Indirect compute**: `DispatchComputeIndirect` in the foliage's set-up (`f_808833e0`, rank 834, with
  `SsboUnitPlacement_DrawList`, `SsboMeshPlacement_*`, `AglOcclusionDepthData`) and three others (`f_81503720`,
  `f_80583e70`, `f_8353aa30`). **read**
- **The `Indirect*AfterOpa` passes**: `f_80364080` (rank 79), from `f_80660320`, binds a program, uniform and storage
  buffers and textures, then issues one `DispatchCompute(8, 1, 1)`. It keeps a ring index at `+0x4a` and runs only when
  `+0x328` is 1. **read** "Indirect" here is likely indirect texturing (refraction that samples the opaque scene),
  not indirect draws. **guess**
- No `MultiDraw*Indirect*` and no `DrawArraysIndirect` is called. **read**

## Functions

### The frame and command buffers

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8066fc80` | `SystemTask_DrawTV(task)` | `Draw(TV)`: the system task's TV draw, on the main thread under the draw tree, reached through the delegate `f_8066fb40`. When `task+0xa34` has bits 0 and 1 set, it builds the frame's draw context at `task+0xf0` (`f_80021a10`), opens the frame's first command buffer (`f_80665980` on the graphics object in `0x84e9fca8`, plus 0x28), and draws the main screen (`f_802ab6c0`, then its vtable `+0x20`), then calls `f_802a46e0` with a rectangle. Hot rank 275 | read |
| `f_836ce750` | `SystemTask_PostDrawTV(task)` | `PostDraw(TV)`: under the same check on `+0xa34`, calls `f_83560df0`, which reaches `f_80527f00` three times through `f_834bf400`: the queued command buffers are submitted here | read |
| `f_80665980` | `CmdSlot_BeginFrame(queues, ctx)` | Opens the frame's first command buffer: `Initialize`, `AddControlMemory`, `AddCommandMemory`, `BeginRecording`, then `WaitEvent`, `SetSamplerPool`, `SetTexturePool`, `SetShaderScratchMemory`, compute settings and timestamps. Calls `f_806668b0` and `f_8007de00`. Hot rank 317 | read |
| `f_806650d0` | `CmdSlot_Begin(…)` | Opens another command buffer in a slot: `Initialize`, the two memories, `BeginRecording`, the memory callbacks, `SetRecordingMode` and a timestamp. Four calls from `f_80660320`. Hot rank 1420 | read |
| `f_8007de00` | `CmdSlot_End(queues, ctx, queue, slot, submitNow)` | Ends a slot's recording: a timestamp, `EndRecording` (the handle kept at `slot+0x50`), the memory used (`+0x4c`, `+0x2c`), `Finalize`. Then, with `submitNow`, `QueueSubmitCommands` on that queue's NVN queue (in `g_GfxSystem`) under its mutex. Otherwise it appends the slot to the queue's pending list for this frame. 11 callers, the recorders among them. Hot rank 3017 | read |
| `f_802ab6c0` | `MainScreen_Draw(task, ctx)` | `Draw(drawMainScreen_)`: returns the screen layer that `Draw(TV)` then draws through its vtable `+0x20`. Hot rank 724 | guess |
| `f_80660320` | `SceneRender_Record` | Records the scene's passes: calls 62 game functions (the targets' allocators of [rendering.md](rendering.md) among them), opens command buffers (`f_806650d0` ×4) and submits (`f_806668b0` ×2), with `SignalEvent` and `WaitEvent` between them, and the shading-rate image. Uses the `Model(IndirectOpaAfterOpa)` and `Model(IndirectXluAfterOpa)` names. Reached through `f_806602e0` (a handler `f_80eeb4b0` registers by hash). Hot rank 41 | read |

### The recording jobs

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80083580` | `DrawTasks_Run` | The bit-13 work of a slot job: the same draw-task loop as bit 15 of `f_80083960`, by task type (`f_8006cd60`, `f_8007d2e0`, `f_8007b520`). Not in `hot_order.txt` | read |
| `f_8007b520` | `DrawTask_RecordOwn(…)` | A type-3 draw task: records its steps (`f_8006e160`) into a command buffer of its own (`Initialize` … `BeginRecording`, then `EndRecording`, `Finalize`, `FlushMappedRange`). Hot rank 615 | read |
| `f_8007d2e0` | `DrawTask_Record(…)` | A type-1 draw task: opens a slot (`f_8007ad40`, which calls `BeginRecording`), records its steps (`f_8006e160`) into it, then closes it with `f_8007de00`. Hot rank 259 | read |
| `f_8006e160` | `DrawTask_RunSteps(…)` | Runs a draw task's list of steps. For each step it either calls the step through its vtable or calls a known step directly (`f_80a47660`, `f_8027e840`, `f_802702e0`, `f_8006f9c0`, `f_80070350`), and may yield. Hot rank 24 | read |
| `f_80a47660` | `RenderStep_Main` | The hottest of those steps: allocates targets (`f_806790d0`, `f_8360b600`: the scene's colour and the reflection), waits on events (`WaitEvent` ×3) and calls 45 game functions. No strings. Hot rank 37 | guess |
| `f_8009bb80` | `GpuBuffers_Update` | The bit-3 work under `f_800998a0`: 12,500 instructions and no strings. It makes and finalises NVN buffers, flushes and invalidates mapped memory-pool ranges, makes 182 indirect calls, draws with `f_8008f840`, and records binding lists (`f_806002e0`). Hot rank 8 | guess |

### Draw passes and lists

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80553cd0` | `DrawPasses_Setup(rec, view)` | Calls `f_80554440` for each of 30 draw types. Hot rank 1979 | read |
| `f_80554440` | `DrawPass_Setup(rec, view, type)` | Builds a draw type's pass descriptor (`Model(...)` names, 35 types in a switch), copies it to `view+0x3830+type*0xd0`, and cuts the type's draw list into chunks. Each chunk becomes a 40-byte batch for the slot jobs (*From draw types to batches*). Hot rank 719 | read |
| `f_80676ef0` | `DrawRecord_BeginFrame(recs, i)` | Starts the frame's record *i* (0x53d8 bytes each, in `recs+0x10`). It copies the current view state (from `+0x18` of the object in `0x84ea1ba8`), drops the previous frame's reference to its slots (an atomic decrement and, at zero, a free list under a mutex), and clears the batch arrays (`+0x3c00` … `+0x3c28`). Hot rank 1004 | read |
| `f_80681320` | `DrawList_DrawInline(…)` | Draws a draw list into the command buffer already open: the loop table at `0x84c29878`, the state and reset helpers, the shape draw. Called by passes that draw inline (`f_80255be0`, `f_806688c0`, `f_8028a2a0`, `f_806811d0`). Hot rank 357 | read |
| `f_8008b3e0` | `DrawLoop_Plain` | Entry 5 of the loop table: no queries and no conditions, with `f_80078fc0` inlined (render state, `CallCommands`, `DrawMeshTasks`). Hot rank 412 | read |
| `f_8008d640` | `DrawLoop_QueryCond` | Entry 160: z-prepass queries and conditional draws around each item, with the shape draw inlined. Hot rank 108 | read |
| `f_80078ce0` | `DrawLoop_Cond` | Entry 32: the conditional loop (`SetRenderEnable`, `SetRenderEnableConditional`), calling `f_80078fc0`. Hot rank 509 | read |
| `f_80078490` | `DrawLoop_Query` | Entry 132: the query loop (`ResetCounter`, `ReportCounter`), calling `f_80078fc0`. Hot rank 1460 | read |
| `f_835c1380` | `DrawList_DebugDump` | Writes a draw list as text: `DepthShadowCascade:0x%x`, `UserPass%d`, `UserQueue%d`, each shape (`Shape:%d [%s]`, `SubMesh:%d`) and its `ModelSortKey` with Runtime, PriorityHint and Priority (*Sorting*). Not ranked | read |

### The shape draw and its binds

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80078fc0` | `ShapeDraw_Draw(shape, ctx, lod, flags)` | Slot `+0x58` of `0x84c2a130`: the common shape draw (*The shape draw*). Hot rank 426 | read |
| `f_800955a0` | `ShapeDraw2_Draw(shape, ctx, lod, flags)` | Slot `+0x58` of `0x84c324d0`. It works out the item's mode (queries, conditions, recorded commands) from `ctx+0x5c` and `shape+0x54` itself, binds the material up to three times (`f_8008fc00`), binds uniform and storage buffers directly, and uses `CallCommands` and the query and condition calls. Hot rank 69 | read |
| `f_8008fc00` | `Material_Bind(ctx, shape, _)` | Binds a shape's material against the context's cache (*The material bind*). Its material record is `[shape+0x60] + index * shape+0x5f`, the index from `ctx+0x62` or the shape. Returns 0 when there is none. 263 callers. Hot rank 35 | read |
| `f_80077d60` | `DrawCtx_ApplyRenderState(ctx, cls)` | Applies a whole render state from the context's class byte: the blend state per colour target, colour state, `SetBlendColor`, the channel mask for eight targets, depth-stencil, polygon, `SetPolygonOffsetClamp`, `SetDepthClamp` and the stencil values. About 35 NVN calls, with no test for what changed. 254 callers (the loop variants). Hot rank 1307 | read |
| `f_80078b10` | `DrawCtx_Reset(ctx)` | When a condition is active (`ctx+0xc0`/`+0xc8`) it calls `SetRenderEnable(1)` and clears it, through the inline path when the flag allows. It then recomputes the state class bytes and forgets every cached bind: the uniform ids at `+0x78` … `+0x98` to −1, `+0x5a` and `+0x60` to 0xff, the program at `+0xd0`/`+0xd8` to 0. 256 callers, not ranked | read |
| `f_83607e80` | `GeometryCulling_DrawIndirect(gc, ctx, view)` | For a geometry-culled shape: `SetPrimitiveRestart` on, `DrawElementsIndirect` (triangles) with the arguments at `+0x14` of the view's 32-byte entry in the culling object's buffer, then `SetPrimitiveRestart` off. Not ranked | read |
| `f_80511fd0` | `ShapeDraw_Init(shape, model, index, heap)` | Slot `+0x10` of the shape vtables. It keeps the model (`+8`), the shape and material indices (`+0x18`, `+0x1a`), the shape resource (`+0x68`) and flags (`+0x54`). When the shape uses `gsys_geometry_culling`, it makes a 0x38-byte culling object at `+0x80` (`f_83607c20`, `f_83607ce0`) | read |
| `f_80521e20` | `ShapeDL_Alloc` | Allocates the shape's memory under the name `gsys::ShapeDL` (`f_80522050`, a memory pool); called from slot `+0x48` (`f_80521a70`). **guess**: the display list that `CallCommands` replays | guess |
| `f_806002e0` | `BindingList_Record(…)` | Records a command buffer of uniform and storage binds and `CallCommands` (`Initialize` … `EndRecording`, `Finalize`), then closes the slot with `f_8007de00`. Called from `f_8009bb80` and `f_835b8550`. Hot rank 3181 | read |
| `f_80364080` | `IndirectAfterOpa_Dispatch(…)` | From `f_80660320`: binds a program (`f_802a1760`), uniform and storage buffers (`f_8029f590`, `f_80296c30`) and textures (`f_802a67c0`), issues one `DispatchCompute(8, 1, 1)`, then calls its object's vtable `+0x58`. A ring index at `+0x4a`; runs when `+0x328` is 1. Hot rank 79 | read |
| `f_808833e0` | `Foliage_DispatchIndirect` | The foliage's GPU work: binds the foliage's buffers by name (`UboCtx`, `SsboUnitPlacement`, `SsboUnitPlacement_DrawList`, `SsboMeshPlacement_Leaf`, `AglOcclusionDepthData`, …) and calls `DispatchComputeIndirect` twice. Hot rank 834 | guess |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84c29878` | `DrawLoop_Table` | 256 pointers to the draw-list loop variants, indexed by the loop mode (`f_80084de0`, `f_80681320`). Entry 5 `f_8008b3e0`, 160 `f_8008d640`, 176 `f_80522a20` ([culling.md](culling.md)) | read |
| `0x84c2a130` | `ShapeDraw_vtable` | The common shape drawable: `+0x10` `f_80511fd0`, `+0x48` `f_80521a70`, `+0x50` `f_836063a0`, `+0x58` `f_80078fc0` | read |
| `0x84c324d0` | `ShapeDraw2_vtable` | A second shape drawable with the same base: `+0x50` `f_836b7ff0`, `+0x58` `f_800955a0` | read |
| `0x84ea2780` | `g_DrawRecords` | Holds the object whose `+0x38` lists the scene records (count `+0x30`) that the bit-10 slot work walks; reached through the GOT slot `0x84ca9cc0` | read |
| `0x84e9fca8` | `g_GfxSystem` | Holds the graphics object: the queues, 96 bytes each (queue *i*'s NVN queue at `+0x30 + i*0x60`, mutex `+0x38`, pending lists from `+0x40`), and at `+0x278` the command-buffer slot pool (count `+0xd0`, 152-byte slots at `+0xd8`, slots per frame `+0xea`). Read directly and through the GOT slot `0x84ca2d60`, whose users pass the object plus `0x28` | read |

## Structures

### A batch (40 bytes, `f_80554440`)

| Offset | Field | Sure |
|---|---|---|
| `+0` | The view the pass draws for (its `+0xc` bit 0 skips the batch) | read |
| `+8` | The draw list | read |
| `+0x10` | The pass descriptor (`view+0x3830+type*0xd0`); its first 0x70 bytes are the key `f_80084de0` hashes | read |
| `+0x18` | The chunk's first item and count (two 32-bit words) | read |
| `+0x20` | A weight: the count scaled by the type's factor | read |

### A draw item

| Offset | Field | Sure |
|---|---|---|
| `+8` | The views it is drawn in (a bit mask) | read |
| `+0xe` | Bits 0 to 3: render-state class (compared with the context's `+0x5a`); bit 4: compared with `+0x5b`, a reset when it changes; bits 6 to 9: the LOD | read |
| `+0x10` | The shape drawable (vtable `+0x58` draws it) | read |

### A draw context (built per batch by `f_80021a10`; see also [culling.md](culling.md))

| Offset | Field | Sure |
|---|---|---|
| `+0x28` | The command buffer's holder (its `+0xd8` is the NVN command buffer) | read |
| `+0x3b` | The geometry processing mode set last | read |
| `+0x50` | Draws counted (each LOD's `+0x268` added) | read |
| `+0x59` | The shape state byte applied last (`shape+0x58`) | read |
| `+0x5a` | The render-state class applied last (`f_80077d60`) | read |
| `+0x5c` | Flags; bit 5 lets shapes use their recorded commands | read |
| `+0x78` … `+0x94` | The ids of the uniform blocks bound last (−1: none) | read |
| `+0x8c` | The texture set bound last | read |
| `+0xd8` | The program bound last | read |

### A command-buffer slot (152 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0x2c` | Command memory used (`GetCommandMemoryUsed`) | read |
| `+0x38` | The recording context (its `+0xd8` the NVN command buffer) | read |
| `+0x4c` | Control memory used (`GetControlMemoryUsed`) | read |
| `+0x50` | The handle `EndRecording` returned | read |
| `+0x90` | Where the timestamps are reported | read |

## Notes

### What LS makes expensive here

- **Batches.** Each batch's `Initialize` and `Finalize` take a lock in the host, and its `EndRecording` copies the whole
  recording ([nvn-calls.md](nvn-calls.md)). The game makes one command buffer per chunk of every pass's list, so the
  smaller the chunks, the more of these. **read** (the code), **guess** (how many a frame)
- **Re-binding at every batch.** A fresh draw context per batch means the first draw of each batch binds its render
  state (about 35 calls), program, blocks and textures again. Under LS each is a record now and a replay step at
  the submit. **read**
- **Recorded commands are copied.** `CallCommands` on a shape's recorded commands is a jump on the console. Under
  LS it copies the recording into the batch, under a shared lock, every time the shape is drawn
  (`inline_recording`, host/nvn_native.c). Shapes with recorded commands are the cheapest draws for the game and not
  for the host. **read**
- **A yield after every batch.** The bit-10 loop calls `nn::os::YieldThread` after each batch. The host answers it by
  giving up the virtual core (`hos_vcore_yield`) and calling `SwitchToThread` (host/hos_thread.c). **read** What that
  costs per frame was not measured.
- **Submits on the recording threads.** Batches for queues 3 and 5 are submitted by the worker that recorded them, and
  a submit replays on its caller under LS. So part of the replay may run on the job threads during the module
  phase, on the critical path. **guess**
- **The inline path.** About 1,800 per-draw places would write commands directly on the console and call NVN under
  LS instead ([nvn-calls.md](nvn-calls.md)). In this page's loops, those are the uniform binds, the queries and
  the conditions. **read** (there)

### Rows elsewhere

`f_80084de0`, `f_8006cd60` and `f_800998a0` have rows on [jobs.md](jobs.md) (`ModuleJob_Dispatch`, `ModuleJob_Post`,
`ModuleJob_RunWithPoll`), and so do `f_80083960` (`StageSlotJob_Run`) and `f_806668b0` (`Draw_Wait`). From this page:
`f_80084de0` records one draw-list batch, `f_8006cd60` records a type-0 draw task, `f_800998a0` runs the bit-3 work,
`f_80083960` dispatches the slot work by the mask at `job+0x10`, and `f_806668b0` ends a slot and submits it, with an
optional fence wait. `f_8008f840`, `f_8002a5f0`, `f_80527f00` and `f_80527e90` have rows on [nvn-calls.md](nvn-calls.md).

## Open questions

- Where the draw lists are filled and sorted, and whether the order is the sort key above. `f_80c7bd00`
  ([culling.md](culling.md)) sets up the `ModelScene(...)` jobs and is not in `hot_order.txt`.
- The chunk limits (`rec+0x3c38`, `rec+0x3c3c`): how many batches, and so command buffers, a frame makes. A count of
  `nvnCommandBufferInitialize` per frame in a run would answer it.
- Whether the slot jobs record frame N's draws while the main thread's `Draw(TV)` handles frame N or N−1. The draw
  records are buffered per frame (0x53d8 bytes each), which suggests a pipeline.
- What `f_8009bb80` (rank 8) writes, and how often it invalidates mapped ranges in play: each invalidate gives up the
  virtual core under LS.
- Whether the per-model matrices are a uniform block that `f_8008fc00` re-binds for every model.
- The bits of the loop mode other than 5 and 7, and the 30 draw types' factors.
- The other slot work (bits 0, 1, 2, 5, 6, 8, 12) and `f_800766e0`, `f_8007c120`, `f_8007e200`, `f_800738e0`.
- What draws through `f_801a46a0` and `f_82c03470` (`DrawMeshTasksIndirect`), and whether `f_80364080` is the refraction
  pass.
