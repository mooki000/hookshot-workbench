# Textures: streaming and residency

How the game makes, fills and lets go of its textures, how the terrain streams its tiles, and what LS's Vulkan
renderer does with all of it: when a texture becomes a Vulkan image, where its texels come from, what the texture budget
evicts, and what can come back wrong. Written for the ground textures that garbled after a while of play and came back after
a restart (issues 107, 386, 993, 1717): fixed by #2545 (merged 11 November 2026, waiting for a release), with one case
left open, a card at the texture budget's limit. Looked at in November 2026.

**LS code:** `gpu/vk/vk_texture.cpp` (`texture_get`, `texture_touch`, `texture_frame`:
the budget and eviction), `gpu/vk/vk_memory.cpp` (which guest pages changed),
`host/nvn_native.c` (memory pools, textures, `MapVirtual`), `gpu/vk/vk_vma.cpp`
(the driver's allowance)
**See also:** [graphics.md](graphics.md) (the render-target pool), [maps.md](maps.md) (how a map loads),
[culling.md](culling.md), `docs/texture-staging-publication.md`,
`docs/ui-texture-upload.md`

## In short

1. **Most textures come with their resources.** A `.bntx` (`Tex/%s.bntx`, `%s/%s.bntx`) is loaded into a memory pool,
   and the game makes each texture over its bytes (`nvnTextureInitialize`) and registers it (`nvnTexturePoolRegisterTexture`).
   Their texels are in the game's memory, so the renderer can always upload them again. **read**
2. **The terrain draws its own textures.** The terrain system (`tera`, TeraTerrain) keeps a grid of chunks around a centre
   that follows the camera. As a chunk comes into range the GPU draws its tiles' textures (height, normal and material:
   `tera_height`, `tera_normal`, `tera_material`), from compressed data staged in temporary textures
   (`tera::temp_for_compressed`). As it leaves range the game finalizes them; when it comes back the game makes a texture
   over the same storage again and samples it **without drawing it again**, because on the console the texels are still in
   that memory. **read** (the renderer's notes on issues 107 and 386 saw the same: the tiles' 260 x 260 RG8 normal maps)
3. **LS kept the GPU's texels only in the Vulkan image.** The renderer makes images lazily, when a draw first
   samples or draws into a texture, and fills them from the game's memory; a texture the GPU drew never reached that
   memory. That broke the terrain twice: the game **moves a chunk's tiles to another block with a CPU copy of their
   memory**, and that copy was all zero (issue 1717); and a drawn tile evicted and made again came back empty (issues
   107, 386). Either way the ground stayed wrong until the game drew the tiles again (a title visit, a restart). **tested**
4. **What protects them now:**
   - **Drawn write-back (#2545):** two frames after the GPU draws a small texture with a mip chain in one frame (the tiles:
     260 x 260 RG8, nine levels), its texels are written into its memory, but only where that memory still holds zeros or
     its own last write-back (anything the CPU wrote since stays). `RX_VK_NO_DRAWN_WRITEBACK=1` turns it off.
   - **Keep rule:** a finalized texture the GPU drew is not evicted if it is small, drawn in one frame and its storage not
     drawn over since, up to `RX_VK_KEEP_FINALIZED_MB` (512 MB).
   - **Write-back before eviction** for live (not finalized) ones that can be written back. **read**
5. **The case left open:** a card at the texture budget's limit (one report: a 16 GB card at the fixed 6 GB budget,
   191 eviction passes). The runs below, on `main` with #2545, did not reproduce it in Kokiri Forest. **tested**

## How the game makes textures

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80065b10` | `Texture_Make` | Makes a texture over storage it is given (`nvnTextureInitialize`); the render-target pool's allocator (`f_8005ed00`, [graphics.md](graphics.md)), the occlusion depth buffer (`gfx::OcclusionDepthBuffer`) and several render passes call it | read |
| `f_800634b0` | `Texture_MakeAndRegister` | Makes a texture and registers it in the texture pool; called by the sky (`SkyInscatter`) and other rendering set-up | read |
| `f_800662e0` | `Texture_MakeAndRegister2` | The same pair, for render passes' targets (`bilateral_grid_tmp` and others) | read |
| `f_8348fcc0` | `Device_MakeSystemTextures` | Makes and registers textures as the NVN device is set up (its caller uses `nvnDeviceInitialize`) | guess |
| `f_83a6d8f0` | `Texture_Upload` | Fills a texture from the CPU: `nvnTextureWriteTexels` and `nvnCommandBufferCopyBufferToTexture` (the game's only other place to write texels from the CPU is `f_8353d560`) | read |
| `f_8353d560` | `Texture_WriteTexels` | Writes texels into a texture from the CPU (`nvnTextureWriteTexels`) | read |
| `f_83b82200` | `MovieRenderer_Upload` | The movie renderer's frames, copied into a texture (`nvnCommandBufferCopyBufferToTexture`) | read |

Finalizing a texture (`nvnTextureFinalize`) does not free its storage: the memory pool's owner does that, or reuses it. The
render-target pool maps physical pages into a virtual pool and back as targets come and go (`f_8005ed00`).

## The terrain

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_809dc6a0` | `TeraScene_Enter` | Uses the label "tera::Scene::enter": the terrain scene as a map is entered | guess |
| `f_809e24c0` | `TeraScene_MakeHeap` | Makes the terrain scene's heap ("Create Terrain Scene Heap") | read |
| `f_80f6cf50` | `TeraFileResourceMgr_Init` | Uses "tera::FileResourceMgr": the terrain's file resources | guess |
| `f_80f6d830` | `TeraFileResourceMgr_SetHeaps` | Splits the heap it is given into "Terrain GPU Res" and "Terrain Host Res" by a ratio (`s0`) | read |
| `f_809e38d0` | `TeraResourceHolder_Init` | Uses "tera::ResourceHolder" | guess |
| `f_80387f40` | `TerrainResourceHooker` | "TerrainAresResourceRequestProcessHooker - Prefetch": fetches terrain resources ahead of need | guess |
| `f_83aca6a0` | `Tera_UpdateGrid` | Allocates a texture named "tera::update_grid" from the render-target pool (`f_8005ed00`): the GPU's work target when the grid around the centre changes | read |
| `f_83aca900` | `Tera_UpdateFrame` | Allocates a texture named "tera::update_frame" from the same pool, each frame's terrain update | read |
| `f_809f3900` | `Tera_DrawCompressed` | Allocates "tera::temp_for_compressed" from the render-target pool, sets a viewport (`f_802a46e0`) and draws: the GPU writing terrain textures from compressed data | read |
| `f_80d7e070` | `Tera_PolishCompressed` | Allocates "tera::temp_for_compressed_in_polish" from the same pool and fills it from the CPU (`f_83a6d8f0`) | read |
| `f_80942d20` | `Tera_Programs` | Names the terrain's shader programs: `tera_material`, `tera_normal`, `tera_height`, `tera_height2`, `tera_bake_gi`, `tera_bake_ao`, `tera_custom`, and the passes `OpaqueNear`, `OpaqueMiddle`, `OpaqueFar`, `ZOnly` and their `…Deformable` forms ([shaders.md](shaders.md)) | read |

The terrain's script nodes say how the rest of the game waits on it: `SeqExecuteWaitTerrainResourceStable`,
`ExecuteTerrainWaitForLoading`, `ExecuteTerrainCalcCenter` (where the grid is centred) and `SeqOneShotCreateTerrainSystem`.
## The game's own texture cache

Model and material textures (`Tex/%s.bntx`, `MaterialAlb`, `MaterialCmb`) are loaded on demand by a texture resource
manager (`shaftTextureResourceMgr`) into **agl::TextureDataMgr** (the singleton `[0x84ea17e8]`), a cache of texture data
with its own settings: `TexatureHandleNum` (sic), `TextureCompactionJobNum`, `TextureCacheRejectionThreshold` and
`TextureUserDataList`. **read**

- **It compacts its memory.** A task named "aglTextureDataMgr::Compaction" moves texture data to close the gaps left by
  textures it let go, with `memcpy` on the CPU, in time slices under a reader-writer lock (it checks the clock and yields
  between them). Under LS those copies are ordinary CPU writes, so a moved texture is simply uploaded again from its
  new place: correct, at the cost of uploads while the compaction runs (a possible source of small hitches). **read**
- **A script node clears it:** `SeqOneShotClearTextureCacheAll` empties the cache's list under its lock. Which flows call
  it (the title, a map change) is not known. **read**

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_832ee510` | `SeqOneShotClearTextureCacheAll_Name` | The script node SeqOneShotClearTextureCacheAll's name (vtable `0x84bce0c8`) | read |
| `f_832ee450` | `SeqOneShotClearTextureCacheAll_Evaluate` | Slot 39: calls `f_8079bb50` on the cache and marks the node done | read |
| `f_8079bb50` | `TextureDataMgr_ClearAll(mgr)` | Under the cache's lock (`+0x3d0`), takes each entry off its list (head `+0x28`, count `+0x30`, the link's offset in an entry `+0x34`) and frees it (`f_8079bbe0`) | read |
| `f_831cb1f0` | `TextureDataMgr_ReadParams` | Reads the cache's settings: `TexatureHandleNum`, `TextureCompactionJobNum`, `TextureCacheRejectionThreshold`, `TextureUserDataList` | read |
| `f_80f6c580` | `TextureDataMgr_Init` | Sets the cache up (its heaps: `TextureCompactionWorkHeap`) and starts the compaction task, its work function at `0x807da3f0` | read |
| `f_807da480` | `TextureDataMgr_CompactStep` | One step of the compaction, under its lock: a pass that picks what to move (`f_807da590`) and the moves (`f_807dbf90`, `f_807db4a0`, `f_807dbd60`) | read |
| `f_807dbf90` | `TextureDataMgr_CompactMove` | Moves texture data with `memcpy`, a slice at a time | read |

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84ea17e8` | `g_TextureDataMgr` | The texture cache (agl::TextureDataMgr) | read |

## What LS does

Read from the code at `main` 51da0464 (November 2026); the function names are in `gpu/vk/vk_texture.cpp` unless said.

- **Pools.** `nvnMemoryPoolInitialize` uses the game's memory in place and watches the CPU's writes to it. A virtual pool
  gets a GPU range, and `MapVirtual` maps pages into it later (host/nvn_native.c).
- **Textures.** `nvnTextureInitialize` makes no Vulkan image: the image is made the first time a draw samples or draws into
  it (`texture_get`, keyed by address, size, format, levels and layout). Uncompressed images start cleared to 0; BC and ASTC
  images start with whatever the GPU memory held.
- **Filling.** `texture_touch` uploads from the game's memory, and again for any 4 KB page the CPU has written since
  (pages stamped with a generation as each submission is recorded, vk_memory.cpp). A texture over storage that render
  targets last drew, and that the CPU has not written since, is not uploaded: the memory holds nothing the GPU's image did
  not replace (`gpu_owned`).
- **Finalizing** frees nothing: it marks images the GPU drew at that address as finalized (`released`).
- **Drawn write-back** (`drawn_writeback.h`, #2545): small mip-chained textures the GPU drew in one frame have their
  texels copied into the game's memory two frames later, with no wait for the GPU (a later frame writes them once the
  GPU is past the copies), and only into memory still as the drawing left it.
- **The budget.** `texture_frame` works out a budget every 10 presents: `RX_VK_VRAM_MB` if set; otherwise 45% of the
  driver's allowance on cards allowed up to 8.5 GB and 70% above, at most 6 GB, or a quality preset's figure; 2.5 GB on
  integrated GPUs; lowered further when the whole process nears the allowance. (docs/development.md still gives only the
  70% rule.)
- **Eviction.** Over the budget, the least recently used textures go, down to 7/8 of it, each unused for at least 60
  frames (`RX_VK_EVICT_MIN_UNUSED`) and done on the GPU. A live texture the GPU drew is kept for a while or written back
  into the game's memory first if it can be (`flushable`: 16 MB or less, not ASTC/BC3, not sparse); a finalized one is
  only kept by `keep_finalized`, and otherwise goes **with no write-back**.
- **Made again.** The next draw that needs an evicted texture makes a new image and fills it as above: from the game's
  memory, or not at all over storage the GPU drew.
- **The "render-target sweeps"** (the tens of thousands of textures "retired" in some logs) are the transient render
  targets the game draws over every frame, let go every 10 presents (`RX_VK_DEAD_EVERY`); they are not a problem in
  themselves.

## What can still come back wrong

With #2545 in, ranked by how well each fits a garbling report after the next release:

1. **A finalized terrain tile evicted and made again empty.** The keep rule fails for it (over 4 MB, drawn in more than one
   frame, its storage drawn over or its `gpu_owned` entry gone) or the 512 MB cap is passed. Log signature with
   `RX_VK_LOG_REMADE=1`: "made again … (rendered then)" and "sampled EMPTY", and "kept finalized textures over 512 MB" lines.
   Test: `RX_VK_KEEP_FINALIZED_MB=4096` or `RX_VK_NO_EVICT=1` should make it go away.
2. **An upload that failed, marked done and never retried.** Under memory pressure a staging arena can fail ("staging: no
   64 MB arena could be made"); BC images then show the GPU memory's old contents. Signature: those lines near when it
   starts.
3. **Stale ownership.** `gpu_owned` entries outlive the render targets that made them, and the map drops its lowest address
   when full (65,536 entries): a texture the game wrote from the CPU could be skipped as "rendered storage", or a drawn one
   uploaded from stale memory. Signature: watch lines "NOT uploaded (over storage the GPU rendered)" for a texture never
   drawn (`RX_VK_WATCH_DIM`); `RX_VK_UPLOAD_ALL=1` would fix it.
4. **A missed CPU write.** Looks well guarded; `RX_VK_VERIFY_DIM` logs "MISSED WRITE" if not.

Note that `RX_VK_LOG_REMADE` only reports a texture made again within 600 frames of its eviction, and its list is cleared
above 20,000 entries: a tile that comes back after a long walk can be missed.

## Measurements

Kokiri Forest, the first save, about four minutes of walking and turning after loading; RTX 3080, 2048x1152;
`RX_VK_TEST_ALLOWED_MB=3000` (the process used ~6.5 GB, so the budget fell to 0 and eviction ran all the time) and
`RX_VK_LOG_REMADE=1`.

| Run | Made again | Terrain tiles sampled again after being finalized | Sampled empty | Picture |
|---|---|---|---|---|
| keep rule on (default) | 405 | 5 (260 x 260 RG8, 1,200-5,400 frames after drawn), all kept | 0 | normal |
| `RX_VK_KEEP_FINALIZED_MB=0` | (as many) | 1 | 0 | normal |

So the walk did not bring enough terrain chunks back into range to reproduce the problem: a longer walk over open terrain
is needed (Hyrule Field, Lost Woods), still with the keep rule off, to show it on demand before testing a fix.

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84d673a8` | `pfn_nvnMemoryPoolMapVirtual` | The game's pointer to `nvnMemoryPoolMapVirtual` (one call, in `f_8005ed00`) | read |
| `0x84d676b0` | `pfn_nvnTextureInitialize` | The same for `nvnTextureInitialize` (17 places) | read |
| `0x84d673d8` | `pfn_nvnTexturePoolRegisterTexture` | The same for `nvnTexturePoolRegisterTexture` (12 places) | read |
| `0x84d676c0` | `pfn_nvnTextureFinalize` | The same for `nvnTextureFinalize` (10 places) | read |
| `0x84d67748` | `pfn_nvnTextureWriteTexels` | The same for `nvnTextureWriteTexels` (2 places) | read |
| `0x84d67f00` | `pfn_nvnCommandBufferCopyBufferToTexture` | The same for `nvnCommandBufferCopyBufferToTexture` (3 places) | read |

The game never calls `nvnTextureGetSparseTileLayout` or `nvnTextureBuilderGetSparseTileLayout`, so it likely makes no
sparse textures of its own, even though it imports the `nn::gpu` sparse-region calls. **guess**

## Open questions

- Which keep condition a garbled tile fails in the reports' logs: size, drawn in more than one frame, drawn over, or the cap?
- Could the renderer write a finalized tile back into the game's memory safely after all, if only while that memory still
  belongs to the same terrain storage (the crash came from memory the game had reused)?
- Which flows clear the game's texture cache (`SeqOneShotClearTextureCacheAll`), and how often its compaction runs in play.
- How the terrain chooses its centre and range, so a test walk can be planned to bring chunks back on purpose.
