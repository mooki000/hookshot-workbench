# Culling

How the game decides what not to draw, and how well each part of that survives in LS. The game culls in several
layers: z-prepass occlusion queries that gate later passes on the GPU, sphere queries whose results the CPU reads back,
its own meshlet culling in task shaders, and scene managers that cull by area, distance and precomputed data. Looked at
in November 2026, from the binary and from how the renderers answer the game's queries.

**LS code:** `nx2/oot_occlusion.py` (Occlusion Off), `host/nvn_native.c`
(the query and condition commands recorded), `gpu/vk/vk_replay.cpp` (the Vulkan renderer's
answers), `gpu/render/rx_core.cpp` and `gpu/render/rx_nvn.cpp`
(the WebGPU renderer's), `gpu/vk/vk_shader.cpp` (`RX_VK_MESH_NOCULL`)
**See also:** [graphics.md](graphics.md), [jobs.md](jobs.md), [camera.md](camera.md)

## In short

1. **Z-prepass occlusion queries.** For each object the z-prepass draws, the game resets the samples-passed counter,
   draws, and reports the count into that object's 32-byte slot. In Kokiri Forest it **reads the counts back on the CPU**
   a frame or two later and leaves the objects whose count was 0 out of its draw lists: a dumped gameplay frame has 491
   reports and no condition at all, and with every count at 0 the game draws ~565 fewer draws a frame, none of them
   "skipped" by a condition. **tested** The code can also gate later passes on the GPU (mode 1, "render if not equal"),
   but that path was not seen running. **read**
2. **Queries the CPU reads.** Script nodes such as `QueryGenericIsVisibleByOcclusionQuery` ask whether a shape (likely
   a sphere: `UseOcclusionQuerySphere`) is visible. The result is read on the CPU a frame or two later, from a ring of
   three, and "visible" means the count is not 0. **read** The sun's visibility follows the samples-passed counts too
   (PR #487); which reader it goes through was not traced.
3. **LS's default renderer (Vulkan) writes every samples-passed report as 4096.** So no z-prepass query ever culls
   an object and every sphere is always visible. The game still pays for every query. **tested** (PR #487)
   `RX_VK_OCC_QUERIES=1` (branch `fix/vk-occlusion-queries`) gives the reports the GPU's real counts: see
   [Measurements](#measurements).
4. **The game's own meshlet culling** (its task shaders' coarse depth test) runs as written, except where
   `RX_VK_MESH_NOCULL` turns it off for the Steam Deck. **tested**
5. **Area, system, distance and precomputed culling** decide which actors and models exist at all. They don't touch the
   GPU, so the renderer can't break them; they are located below but not yet read.

## How it fares in LS

| Layer | What the game expects | What LS gives it | Effect |
|---|---|---|---|
| Z-prepass queries (Vulkan, the default) | A real sample count per object, read back a frame or two later | 4096, written on the CPU as the replay reaches the report (`RX_VK_OCC_FIXED`, `-1` for a count of 16 a draw) | **Culling off.** Every object is drawn in every later pass, as if nothing hid it |
| Z-prepass queries (WebGPU) | The same | Real counts from WebGPU occlusion queries (`RX_OCCLUSION_QUERIES`, on by default), written after the GPU finishes the batch, so the condition reads the last value | **Works, late.** An object coming out from behind something can stay culled for a frame; past 1024 queries a batch, counts are estimated |
| Conditional rendering | `SetRenderEnableConditional` mode 1 on the GPU | The two words read on the CPU at replay; Vulkan's `VK_EXT_conditional_rendering` is detected but not used | Correct for the values it's given. Only modes 0 and 1 are accepted, and the game only uses 1. Not seen in use in Kokiri Forest |
| Sphere queries (CPU readback) | The count, a frame or two later | 4096 on Vulkan | **Always visible.** The sun counts as visible behind terrain and indoors (below) |
| Occlusion Off (`LONGSHOT_OCCLUSION_OFF=1`) | | The two query settings read 0 (below) | On Vulkan: the same picture (the sun's haze included) and, measured under the same load, the same GPU time and frame rate. Nothing was culled anyway |
| Meshlet culling | Runs in the game's task shaders | Runs; `RX_VK_MESH_NOCULL=<shader key>` replaces the test with "visible" (the Deck's GPU timeout, issue #817) | Works, except where turned off |
| ZCull | A GPU hint | `SaveZCullData` and `RestoreZCullData` do nothing; desktop GPUs have their own hierarchical depth | No effect on the picture |

### What this costs

- **GPU:** with no z-prepass culling, a scene with a lot hidden behind walls or terrain draws all of it in the G-buffer
  and later passes. The ceiling in Kokiri Forest is about a third of the GPU's time ([Measurements](#measurements));
  real counts save a fraction of that, more where more is hidden.
- **CPU:** every object in the z-prepass still records a reset and a report, and every conditional draw a condition
  and a re-enable. That's in the lifted code and in the host's NVN recorder, for no benefit on Vulkan. The NVN library's
  inline-command switch (`0x85ff20a8`, in its zero-initialised data) is never set under LS, so each of these is a
  call through the NVN function table rather than a few words written into the command buffer. **guess**
- **Risk:** where the conditional path runs, the condition is "render if not equal" against a half of the slot the game
  never writes. That half has to stay 0. If it ever held 4096, its object would vanish on Vulkan. **guess** (not seen
  running in Kokiri Forest, so not checked)

### The sun

PR #487 fixed the sun's visibility flickering with the per-draw count, by making every report read 4096 (4 to 6
one-frame brightness spikes per ~2,800 frames in Kokiri Forest, against 30 to 52). The cost is that the sun is now always
visible: whatever the game does when the sun is hidden (fading its glare or haze) never happens. Worth checking in a cave,
indoors, or with the sun behind a mountain, against the Switch. **guess**

## Functions

### The settings

The render pipeline's settings are switches named by the CRC32 of a snake_case name (`z_pre_pass`, `g_buffer`,
`geometry_culling_z_pre_pass`, …). Each is a field with an override flag: when the flag is clear, its getter asks the parent
settings object.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_811cc790` | `RenderSettings_Sync` | A very large function (about 35,000 instructions) that walks 341 settings by hash and copies each between a settings object (`x20`) and its parameter entry (the value at `entry+0x18`): in mode 2 it copies the setting in; in mode 1 it compares and, on a change, copies it and calls the entry's callback (vtable +0x48) and `f_8351e2b0`. Among them `create_occlusion_query` (0x73f48ad3), `create_z_pre_pass_occlusion_query` (0x2b8ea8de) and `create_samples_passed_query` (0xd1eaac2b). Hooked by `install` (nx2/oot_occlusion.py): its six reads of the last two read 0 | read |
| `f_811cc460` | `RenderSettings_CreateOcclusionQuery` | The address of `create_occlusion_query`: `+0x58a`, or the parent's when `+0x605` bit 0 is clear | read |
| `f_80f7acc0` | `RenderSettings_CreateZPrePassOcclusionQuery` | The address of `create_z_pre_pass_occlusion_query`: `+0x596`, or the parent's when `+0x606` bit 0 is clear | read |
| `f_80f7bf70` | `RenderSettings_CreateSamplesPassedQuery` | The address of `create_samples_passed_query`: `+0x58e`, or the parent's when `+0x607` bit 0 is clear | read |
| `f_80f7b620` | `RenderSettings_Parent` | The parent settings object of `settings+0x10`, or 0 | guess |

### The z-prepass queries and the conditional draws

The two loops below walk a draw list (the items' array in `x0`, the count in `w1`, the draw context in `x2`). An item's
query object is found from its shape (`[item+0x10]`): in the query loop `[[shape+8]+0x40 + view*256 + 0x68]`, in the
conditional loop `[[shape+8]+0x58]`, where the view is `ctx+0x38`. About 244 functions reset and report the samples passed
and about 258 set a condition, nearly all of them template copies of these two in 0x835c5000–0x835de000.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_835cd630` | `DrawList_DrawWithQueries` | For each item with a query object (and no `[shape+8]+0x48`): `ResetCounter(SAMPLES_PASSED)`, the item's draw (its vtable +0x58), a mark in the query's issued bytes (`[query+0x20]`), and `ReportCounter(SAMPLES_PASSED)` into slot `index * 32` of the query's buffer (`nvnBufferGetAddress(query+0x50)`). The index is `query+0xc * query+0xf`, plus a value from the shape's vtable +0x1e8 times the view, plus `shape+0x18` | read |
| `f_80522a20` | `DrawList_DrawWithQueries_Generic` | The same loop, not a template copy, near agl::cull::OcclusionQuery's code | read |
| `f_835c5460` | `DrawList_DrawConditional` | When `[ctx+0x18]+0xa` bit 0 is set: when an item's query object differs from the last one (`ctx+0xc0`), `SetRenderEnable(1)`; then, if the query has the view's bit (`query+0x12`, bit `ctx+0x38`), `SetRenderEnableConditional(1, slot)` on the item's slot. Then the item's draw (vtable +0x58) | read |
| `f_80523890` | `OcclusionQuery_Draw2` | Another function that resets and reports the samples passed, next to `f_80522a20` | guess |
| `f_805224d0` | `OcclusionQuery_Type` | Uses the name "agl::cull::OcclusionQuery" | guess |
| `f_80c4da80` | `DynamicOcclusionQueryAllocator_Init` | Uses the name "agl::utl::DynamicOcclusionQueryAllocator" | guess |
| `f_80c7bd00` | `ModelScene_Calc` | ModelScene's frame update. Its labelled steps include "ModelScene(UpdateDynamicOccluder)" and "ModelScene(OcclusionQuery)" (each its vtable +0x1f0) | read |
| `f_835867c0` | `RenderPipeline_Build` | Reads "CreateZPrePassOcclusionQuery" and "OcclusionQueryNum" among the pipeline's parameters (about 12,000 instructions) | guess |
| `f_835927d0` | `RenderPipeline_Build2` | The same two parameters, in a second builder | guess |

### The sphere queries the CPU reads

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_808251a0` | `QueryGenericIsVisibleByOcclusionQuery_Evaluate` | The script query (slot 39 of the vtable `0x84aaace0`). Copies its inputs into the query (a position at `+0x80`, floats at `+0x8c` and `+0x90`), appends the query to the frame's list (a lock-free append), then reads the result with `f_80825460`. Its output is bit 1 of `[query]+0x20` once bit 0 (a result) is set | read |
| `f_82a27400` | `QueryGenericIsVisibleByOcclusionQuery_Name` | The node's name (slot 19) | read |
| `f_80e75580` | `QueryGenericIsVisibleByOcclusionQuery_Params` | Declares its inputs and output (slot 28) | read |
| `f_80825460` | `SphereQuery_ReadResult(query)` | Steps a ring of three per-frame states (`+0x22`, `+0x23`, `+0x24`). A state at 3 has a result: if the buffer's pool is CPU-cached (`nvnMemoryPoolGetFlags` bit 2) it is invalidated first; then the 64-bit count is read from the buffer's CPU mapping (at `index * 32`, or `* 64` for the third) into `+0x18`. `+0x20` bit 0 is set (a result) and bit 1 (visible) unless the count is 0 | read |

### The other layers

Located by the names they use; not read yet. Whether a map uses occlusion culling at all is its `GameBancParam`'s
`IsEnableOcclusionCulling` field ([maps.md](maps.md): `f_8083b770`, `f_81227610`).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_832d4720` | `SceneComponents_Register` | Registers the scene components AreaCullingMgr and SystemCullingMgr, among others | guess |
| `f_82bde830` | `PreComputedCulling_Load` | Formats `PreComputedCulling/RuntimeId/%016llx.bcul`: a map's precomputed culling | guess |
| `f_83ba8630` | `DistanceCulling_ReadParams` | Reads "IsEnabledDistanceCulling" | guess |
| `f_805ca930` | `OcclusionCulling_ReadInvalid` | Reads "IsInvalidOcclusionCulling" | guess |
| `f_83508620` | `OcclusionViewZ_Render` | Uses "RenderOcclusionViewZ": the occluders' depth | guess |
| `f_83307e40` | `SeqOneShotSetOcclusionCullingFieldType_Name` | The script node SeqOneShotSetOcclusionCullingFieldType's name | guess |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84d67f58` | `pfn_nvnCommandBufferReportCounter` | The game's pointer to `nvnCommandBufferReportCounter` (through the GOT slot `0x84cadad8`) | read |
| `0x84d67f60` | `pfn_nvnCommandBufferResetCounter` | The same for `nvnCommandBufferResetCounter` (GOT `0x84cadad0`) | read |
| `0x84d67f70` | `pfn_nvnCommandBufferSetRenderEnable` | The same for `nvnCommandBufferSetRenderEnable` (GOT `0x84cae868`) | read |
| `0x84d67f78` | `pfn_nvnCommandBufferSetRenderEnableConditional` | The same for `nvnCommandBufferSetRenderEnableConditional` (GOT `0x84cae870`) | read |
| `0x84d674a8` | `pfn_nvnBufferGetAddress` | The same for `nvnBufferGetAddress` | read |
| `0x84aaace0` | `QueryGenericIsVisibleByOcclusionQuery_vtable` | The sphere query node's vtable | read |
| `0x85ff20a8` | `nvnPrivate0` | An SDK export (main reads it through `[0x84ca2dd8]`, at 1,871 places): when set, the game writes commands straight into the command buffer instead of calling NVN. The SDK's own `nvnDeviceGetProcAddress` sets it on the console; under LS that code never runs, so it stays 0 and every place makes the call ([nvn-calls.md](nvn-calls.md)) | read |

The NVN loader (`0x812be290`) stores each name's pointer there; the code calls through them.

## Structures

### A query slot (32 bytes, in the query object's buffer)

| Offset | Field | Sure |
|---|---|---|
| `+0` | The samples passed (64-bit), written by the report | read |
| `+8` | The report's timestamp | read |
| `+16` | Never written by the query loop; compared with `+0` by "render if not equal" | read |

### A query object

| Offset | Field | Sure |
|---|---|---|
| `+0xc` | A count (16-bit), a factor of the slot index | read |
| `+0xf` | A count (8-bit), the other factor | read |
| `+0x12` | One bit per view: the view's draws are conditional | read |
| `+0x18` | The number of issued bytes | read |
| `+0x20` | The issued bytes (one per slot, bit 0: a query was drawn) | read |
| `+0x50` | The NVN buffer the slots are in | read |

### A draw context

| Offset | Field | Sure |
|---|---|---|
| `+0x18` | The pass's settings; `+0xa` bit 0 of it turns the conditional draws on | read |
| `+0x38` | The view | read |
| `+0xc0` | The query object of the last conditional draw | read |

### A sphere query

| Offset | Field | Sure |
|---|---|---|
| `+0x18` | The last count read (-1 before one) | read |
| `+0x20` | Bit 0: a result; bit 1: visible; bit 2: from the frame list's `+0x71` | read |
| `+0x22 … +0x24` | The three frames' states (3: a result to read) | read |
| `+0x80 … +0x90` | The sphere (a position and two floats) | read |

## Notes

- The sun model's visibility reads no occlusion count: it is a day/night flag the world manager keeps, and the sun's occlusion for the lens flare and clouds is measured on the GPU by a compute program with no CPU reader found ([environment.md](environment.md)). That answers this page's question about the sun only in part: PR #487's flicker more likely came through geometry culled out of the depth that program reads. **read**
- An actor's `LODParam` has `UseOcclusionQuery` at `+0x88` and `UseOcclusionQuerySphere` at `+0xa5`, and the area-culling fields of its placement are read by `f_805cefb0` ([actors.md](actors.md)). **read**
- The Occlusion Off patch is cLohan's community patch (zortos293/Lichii#641), as Fritchii ships it. Its six sites are
  the three sync paths of `create_z_pre_pass_occlusion_query` and of `create_samples_passed_query` in `f_811cc790`.
  `create_occlusion_query`, the setting before them, isn't touched.
- The game has more culling than this page has read: `GeometryCulling(ZPrePass)` and `GeometryCulling(GBuffer)` (the GPU
  geometry culling, `geometry_culling_*` settings), view-frustum clusters (`ViewFrustumCluster*`, for lights), occluder
  depth (`AglOcclusionDepthData`), and per-actor flags (`UseOcclusionQuery`, `UseOcclusionQuerySphere`,
  `CullingDistance`, `CullOutsideView`, `CullHiddenInsideAndOutsideView`). Where the CPU's view-frustum test for models is
  has not been found.

## Measurements

Kokiri Forest, the first save, standing still about 100 s after loading; headless, 2048x1152, NVIDIA RTX 3080 (driver 621.14); one run of
200 s each, under the bench lock (`E:\Hobby Development\.bench-lock`), in November 2026. "GPU time" is the renderer's own
per-frame figure.

| Reports read | Draws a frame | GPU time a frame | Frame rate | Picture |
|---|---|---|---|---|
| 4096 (the default) | ~1,570 | ~11.5 ms | 26-28 fps | as on the Switch |
| 0 (`RX_VK_OCC_FIXED=0`: the ceiling) | ~1,010 | ~7.5 ms | ~30 fps | nearly everything gone, the sun washed out |
| the GPU's counts (`RX_VK_OCC_QUERIES=1`) | ~1,330 | ~11.0 ms | 27.5-29 fps | the same as the default |

With real counts, about 32% of the reports read 0, and each count reached the game's slot about one recording (a sixth
of a frame) after the report, well inside the frame or two the game waits; none had to be left "visible". The 4096
written at replay still stands until the count lands, so a late count costs culling, never a missing object.

## Open questions

- Pop-in: an object coming out from behind something is drawn again only once a count says so. Worth a run with the
  camera turning, and one indoors, where more is hidden.
- The sun with real counts: the haze should now dim when the sun is hidden. Worth checking in a cave or with the sun
  behind a mountain, against the Switch.
- Which scenes, if any, use the conditional path (`f_835c5460`), and is the second half of their slots 0?
- The gain and the picture on AMD (Tier 1: half the reports), a Steam Deck and an integrated GPU, before `RX_VK_OCC_QUERIES` could be on by
  default.
