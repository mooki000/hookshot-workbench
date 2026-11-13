# Resolution, output size and graphics memory

How the game sizes its picture and its 3D: the dynamic resolution path and the fixed one, the scale table, the output size
and where it is read, and the memory the render targets come from. Also the viewport helper the save picture is drawn
through. Looked at for the Insert menu's Resolution and Output resolution rows, native scene resolution and widescreen.

**Longshot code:** [nx2/oot_patches.py](../../nx2/oot_patches.py) (`patch_dynres`, `fixed_scale`, `apply_output_size`),
[nx2/oot_wide.py](../../nx2/oot_wide.py), [host/nn_applet.c](../../host/nn_applet.c) (`hos_output_size`, `hos_rt_pool_size`)
**See also:** [frame-time.md](frame-time.md)

## The 3D's scale

The engine's config flag at `+0x94` picks the path: one follows the GPU-time controller (dynamic resolution, which made a
new set of render targets at each size, about 11 images a frame at times), the other renders the 3D at a fixed scale. Lichii's
"Max Resolution" patch (main+0x805BD0) and `patch_dynres` take the fixed path.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_831f3200` | `Scene_Update` | The scene's update. Recomputes the 3D's scale (`+0xe5c = scale * +0xe58 / +0xe50`) only while `+0xe2c` bit 3 is set; the dynamic path sets it on a level change. On the fixed path the flags `+0xe20` bits 3 and 4 put a hardcoded 0.75 or 0.5 in place of the scale. Its copy of the picture's size is at `+0xb68`. Hooked by `fixed_scale`: a cached scale that differs from the one asked for sets the dirty bit (Hookshot's `patch_scene` did it too, until #2042) | tested |
| `f_831f41d0` | `Scene_RequestRefresh` | Called for the fixed path's flag 1. Its bit 3 is cleared by the apply block in the same frame, before any recompute, so the fixed path kept the dynamic path's last scale (0.75 docked: 1536×864) until the fix | tested |
| `f_806702e0` | `GraphicsScale_SelectMode` | Picks the graphics scale's mode and tells the resources about a change. The scale table is (1.0, 0.8, 2/3) for each axis, in two profiles; the game runs in mode 1, so the fixed path's 100% sized the scene at 0.8 of the picture (2048×1152 in 2560×1440). Its factor times the base is the frame, capped by the scale's display class (docked 3840×2160 at scale 0). Hookshot's `patch_config` made mode 1's 0.8 1.0, until #2042 removed it | tested |

## The output size

The Insert menu's Output resolution (2560×1440 as shipped). `apply_output_size` changes each of these.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_81222ce0` | `nnMain` | Puts the display size in the framework's create arg (`+20`/`+24`, the framework's `+156`/`+160`); the window textures are made at it. The picture's 2560×1440 is set at 0x81202ee4 | tested |
| `f_810a7b70` | `DisplaySize_Setup(…, x1 = config)` | Sets up the display-size object (`[[0x84ca29c0]+216]`) from a config: its docked size list (`+8`, first 2560×1440) is the frame's base (`+244`) | tested |
| `f_80ea2f30` | `Scene_MaxSize` | The 3D's largest size: ResolutionParam's BaseResolution (or 1920×1080 as constants), 3/4 of 2560×1440. Above it the game takes a smaller upscale network (1/3: 1280×720 at 4K) | tested |
| `f_831cca50` | `ResolutionParam_Get` | Called by `f_80ea2f30` (returning at 0x80ea2ffc); the pair at its result is the base size | read |

## Render target memory

The render targets' GPU pool is 0x13100000 bytes, sized for 2560×1440: a 3840×2160 3D at the 4K output ran out of it.
`apply_output_size` scales the pool with the output's pixels (687 MB at 3840×2160), and grows the "Graphics" heap by as much.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_836ce9d0` | `Graphics_MakeTargetPool` | Reads the pool's size (the graphics config's `+1464`), takes its storage from the "Graphics" heap and makes a physical pool and a virtual one over it, through `f_80c4a1c0` | tested |
| `f_80c4a1c0` | `TargetPool_Create` | The first of the chain `f_80c4a1c0` → `f_80c49070` → `f_80c49380` → `f_811c9ba0` that makes the pools. It also makes agl's `DynamicUniformBlockAllocator`, `DynamicShaderStorageBlockAllocator` and `DynamicUniformBlock` ([shaders.md](shaders.md)) | read |
| `f_80c49070` | `TargetPool_Create2` | The second of the chain | read |
| `f_80c49380` | `TargetPool_Create3` | The third of the chain | read |
| `f_811c9ba0` | `TargetPool_Create4` | The last of the chain | read |
| `f_8005ed00` | `DynamicTextureAllocator_Alloc` | The pool's allocator, agl::utl::DynamicTextureAllocator (its `+0x28` the size, 0x13100000): maps physical pages into the virtual pool (the game's one call of `nvnMemoryPoolMapVirtual`) and makes the texture there (`nvnTextureInitialize`); the terrain's temporary textures come from it too ([textures.md](textures.md)). When the pool ran out, the next target came back null and the game crashed here | tested |
| `f_83b7a2c0` | `ModuleHeaps_Setup` | Makes the module heaps in two passes, through `f_83b7b340`; the first pass's Graphics heap is 0x34900000 bytes | read |
| `f_83b7b340` | `ModuleHeaps_Make` | Makes the module heaps (the Graphics heap kept at 0x348ffe20, which had under 16 MB to spare), creating each through `f_809df710`. Then takes each heap's use (its vtable +120) off its module's budget (the configured size, a table of slots) and aborts below zero (`f_8351c280`) | tested |
| `f_809df710` | `Heap_Create` | Takes a heap's size in `x0` (called from `f_83b7b340`, returning at 0x83b7b440); likely makes the heap | guess |
| `f_8351c280` | `Abort` | The abort `f_83b7b340` calls when a module is over its budget | read |
| `f_836cf4b0` | `Graphics_Init` | The graphics init; calls `f_80b72340` | read |
| `f_80b72340` | `Graphics_MakeSpareHeap` | Makes a heap of what the Graphics heap can still give, through `f_809df700`. It would take the growth too (0x1b6cb48 bytes as shipped, 0x1996cb48 grown) and leave too little for the pool, so it keeps its shipped size | tested |
| `f_809df700` | `Heap_CreateFrom` | Takes a heap's size in `x0` (called from `f_80b72340`, returning at 0x80b723ac); likely makes the heap | guess |
| `f_80672260` | `SceneBuffer_PickFormat` | Picks the format of the scene buffer the game tone maps into (at 0x80672340: RGBA16F, 44, under two of its flags, else R11G11B10F, 26) and makes the buffer again when the one it has differs (`+440`). R11G11B10F has no sign and 6 bits of mantissa, so native HDR banded. Hooked by `patch_scene_format`: with `hos_scene_rgba16f` (the Insert menu's HDR at Native with the PsychoV35 tone mapper) both choices are RGBA16F, after RenoDX's zelda-oot-oz patch | read |

## The save picture's viewport

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_802a46e0` | `Gfx_SetViewportScissor` | The viewport-and-scissor helper: SetScissor, then SetViewport(x, y, w, h) in `w1`-`w4` from `[x8]`, called by `blr x8`. The pause menu draws the whole picture into a 543×306 texture (the save's JPEG) through it; `nx2/oot_wide.py` makes that one viewport wider or taller than the texture away from 16:9 (issue 952) | tested |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84ca29c0` | `g_DisplaySizeHolder` | The display-size object is `[[0x84ca29c0]+216]` | tested |

## Structures

### The scene (from `f_831f3200`)

| Offset | Field | Sure |
|---|---|---|
| `+0xb68` | A copy of the picture's size | read |
| `+0xe20` | Flags: bits 3 and 4 put a hardcoded 0.75 or 0.5 in place of the scale | tested |
| `+0xe2c` | Flags: bit 3, recompute the scale | tested |
| `+0xe50`, `+0xe58` | The terms of the scale's recompute | read |
| `+0xe5c` | The 3D's scale | tested |

### The display-size object

| Offset | Field | Sure |
|---|---|---|
| `+244` | The frame's base | tested |
