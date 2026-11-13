# Shaders

How Ocarina of Time finds, chooses, makes and binds its shader programs, and what LS's Vulkan renderer then does
with each one: from the Ampere (SM86) code in the game's `Nin_Ounce_NVN2` archives, through Eden's shader recompiler, to
a Vulkan pipeline. The game has three shader systems side by side: nn::g3d's shader archives (`.bfsha`) for its models,
terrain and UI, agl's own program archives (`sharcb`) for its passes, post effects and compute work, and the networks and
middleware that carry their own (NDSR, the movie upscaler, the particle effects). Looked at in November 2026: the game's
half by static analysis of the binary (no builds, no runs), the renderer's half from LS's source (main at
c67cd4a0). Hot ranks are positions in `hot_order.txt` ([hot-path.md](hot-path.md)).

**LS code:** `gpu/vk/vk_shader.cpp` (the translation, the per-shader fixes, the
preparation), `gpu/vk/vk_shader.h`, `gpu/vk/vk_draw.cpp` (pipelines,
the precompile, the waits), `gpu/vk/vk_bank.cpp` (the driver's constant banks),
`gpu/vk/vk_coopmat.cpp` (the networks' matrix multiplies), `gpu/vk/eden`
and `gpu/vk/sirit`, `host/nvn_native.c` (the program calls),
`host/rx_backend.c`, `nx2/shader_census.py` (the archives read before
the first start), `nx2/pipeline_states.py`. The program cache, the loading and the vendor fixes are in `gpu/vk/` too (`vk_progcache.cpp`, `vk_programs.cpp`,
`program_life.h`, `vk_vendor.h`, `mesh_mode.h`, `translate_wait.h`, `driver_faults.h`), named below without links.
**See also:** [drawing.md](drawing.md) (the per-draw material bind and its cache), [nvn-calls.md](nvn-calls.md) (every
program call and what LS does with it), [rendering.md](rendering.md) (the passes, and the programs the renderer
recognises), [culling.md](culling.md) (the meshlet culling task shader), [textures.md](textures.md) (the terrain's
programs), [effects.md](effects.md) (the particles' compute programs), `docs/design.md`,
`docs/development.md`

## In short

1. **The game makes its programs lazily, and not in its hot path.** No function that makes an NVN program
   (`nvnProgramInitialize`, `SetShaders`, `SetShadersExt`, `InitializeFromBuilder`) is in `hot_order.txt`. A model's
   variation is made when its material is set up (`f_80443000` → `f_80442f00` → the archive's set-up callback
   `f_814f26c0`); an agl pass's program is made at its first bind after it changed (`f_802a1760` → `f_80654d70` →
   `f_806569a0`), on whichever thread records that pass. So `nvnProgramSetShaders` reaches the host during play, when
   something new appears, and not only at the title. **read**
2. **A variation is picked by a key, and found by a binary search.** A g3d shading model lists its programs by key:
   a few 32-bit words of static options (fixed per material) and dynamic ones. A material's choices (`gsys_assign_type`,
   `gsys_weight`, the meshlet attribute masks, `gsys_meshlet_process_type_*`, and the shader's `o_*` options) are found
   by name in the model's option dictionary and packed into the key (`f_814f20b0`), and the program is the one whose
   static words match (`f_814f25e0`). A key no program has selects nothing. **read**
3. **Each variation can hold three kinds of program.** The set-up callback tries the binary first, then the
   intermediate and the source ones (`f_814f26c0`, offsets `0x10`, `0x8`, `0` in the variation). On the Switch 2 only the
   binary (SM86 code with its 0xd8-byte NVN2 control section) is there to use. **read** (the order), **guess** (that the
   others are always empty)
4. **What costs per frame is binding, not making.** The material bind (`f_8008fc00`, rank 35, [drawing.md](drawing.md))
   and NVN's `BindProgram` wrapper (`f_812bd9d0`, rank 574, [nvn-calls.md](nvn-calls.md)) are the hot ones; agl's
   program, uniform-block and sampler binds follow (`f_802a67c0` rank 627, `f_802a1760` 841, `f_8029f590` 846,
   `f_80296c30` 1111). **read**
5. **Two recording threads can meet on one agl program.** Its dirty word is taken with an atomic; a second thread that
   finds it being updated sleeps in a loop until it is done (`f_802a1760`). Under LS the sleep is a host call, and
   the update can be a program's first making, which the host answers by asking the renderer to load what it kept.
   **read** (the code), **guess** (how often it happens)
6. **On the port's side, every program is keyed by its code.** A stage's code key is a hash of its control section and
   code area (`code_key`, vk_shader.cpp). Every per-shader switch and fix takes these 16-digit keys
   (`RX_VK_MESH_NOCULL=2c56dc1006942f74`). The game's own variation keys never reach the renderer: it sees only the code
   the chosen variation points at. **read**

## Part 1: the game's shaders

### Where the programs come from

| Files | What they hold | Read by | Sure |
|---|---|---|---|
| `Shader/%s%s%s.%s.bfsha`, filled with an archive's name (a list of up to 32), `.Product` (or `.All`), `.product` and `Nin_Ounce_NVN2` | nn::g3d shader archives (BNSH inside): the shared model shaders | `f_83b728f0`, through the resource system (`f_801b0950`) | read (the format and its arguments), guess (the names on disk) |
| `Shader/ExternalBinaryString.bfres` | Shared strings for the archives' binary data | `f_83b728f0` | guess |
| `Lib/agl/agl_resource.Nin_Ounce_NVN2.release.sarc`, `Lib/gsys/gsys_resource.….sarc` | agl's and gsys's own archives (`sharcb`, magic BAHS: the census reads them) | `f_83b728f0`; agl's programs by `f_80b72180`, gsys's G-buffer and utility ones by `f_80b7cc30` | read |
| `gsys_minimum.bfsha`, and gsys's application packages (`gapkginfo`, `gapkglist`, `agl_sharc`, `%s_%02d`) | gsys's minimal model shader; packaged archives | `f_80c75b70`, `f_80722da0` | read (names), guess (roles) |
| A model's `.bfres` | Its textures (`textures.bntx`) and, when it has one, an embedded file ending in `bfsha` | `f_804563b0`, `f_80455bc0` | guess |
| `Layout2Shader/MergeShader.%s.Nin_Ounce_NVN2.bfsha` | The UI's (Layout2) shaders | `f_80fd0d10` | read |
| `Shader/ApplicationPackage…` | agl archives named `AglShader` and `FoliageShader` | `f_80b696d0` | guess |
| `Lib/Terrain/tera_resource.Nin_Ounce_NVN2.release.sarc` | The terrain's resources, its agl programs (`tera_common`, `tera_develop`, `tera_debug`) among them | `f_80fd42f0`; programs by `f_80b6c8a0` | read |
| `Lib/agl/agl_ndsr.pck`, `agl_ndsr_vr.pck`, `agl_ndsr_vr_light.pck` | NDSR networks (magic NDSR), with the "SMAA_For_NDSR" library's own compute programs | `f_80f71360`, `f_80f71a00` | read |
| `Movie/Ndve/…` | The movie upscaler's networks (NDVE) | `f_82105e20` | guess |
| `Effect/*.vsetb.byml.zs` | Particle shaders (a BNSH inside each set) | nn::vfx3 ([effects.md](effects.md)) | guess |

The binary has 10,154 `Nin_Ounce_NVN2` shader files in its romfs (`docs/design.md`); LS's census walks
the same folders (`Shader/`, `Layout2Shader/`, `Lib/`, `Movie/`, `Effect/`) and reads each of these formats. Every
archive is loaded at start-up or with its model, and none of the loaders is ranked. **read**

### Choosing a variation: options and keys

The model shaders are NintendoWare G3d2 (`SDK MW+Nintendo+NintendoWare_G3d2-22_2_8-Release`, referred to by
`f_80456640` and `f_80796e40`). An archive has shading models (256 bytes each); a shading model has options (40 bytes
each), programs (64 bytes each) and a key table that gives each program's key: the static words, then the dynamic ones.

1. **Set-up** (`f_80796e40`): the archive's program callback is set to `f_814f26c0`, each shading model gets its mutex,
   its code is given GPU addresses (`f_812c3d60`), and every program is marked as needing set-up (`+0x34` bit 0).
2. **A material builds its choices** (`f_80443000`): a list of option name and choice pairs, from the material and from
   gsys's own: `gsys_assign_type`, `gsys_weight`, `gsys_assign_material`, `gsys_meshlet_vertex_attribute_bitmask_0` to
   `_2`, and, by a setting of the material's shader object (`+0x22c`: 1 or 3; its selector is at `+0x90`),
   `gsys_meshlet_process_type_passthrough` or `gsys_meshlet_process_type_use_prepass`. Each name is looked up in the
   model's option dictionary, then the choice in that option's dictionary (NintendoWare's dictionaries, searched bit by
   bit), and written into the key.
3. **The selector** (`f_8044f300`, `f_80442f00`) holds a static key, a dynamic key and the last key it looked up. When
   the static key differs from the last one, it searches the key table (`f_814f25e0`): a binary search over the
   programs' static words. The program found is set up if it still needs it (`f_814f1f50`, under the model's mutex).
   Not found: the last key is filled with `0xff` and no program is selected.
4. **The UI does the same** with its own options (`f_8155dc50`: `o_IsProceduralShapeEnabled`, `o_TextureMappingType0`…,
   `o_IsSlicingOnlyOutline0`…, `o_PaneCenter` and others) whenever a Layout2 material is made (`f_8155e9c0`, hot rank
   965: the UI makes materials during play). **read**

The model shader's static options include `o_inner_material`, `o_water_volume`, `o_water_type`,
`o_indirect_after_opa`, `o_enable_ssr`, `o_enable_miasma`, `o_enable_vertex_deformation`, `o_far_material`,
`o_deferred_shading_tile_shadow` and about twenty more; their render info has `r_far_material`,
`r_polygon_offset_force`, `r_enable_cascade_*` and others (each name is a static string the game initialises once,
0x831ee520–0x831ef5a0). The render state a material asks for comes from its `gsys_render_state_*` values
(`f_80451d60`). **read** (the names), **guess** (that all of them are options the key carries)

### Making the NVN program

- **g3d programs** (`f_814f26c0`): for the program's variation, each of its three kinds is made with nn::gfx's shader
  initialize (`f_812c42c0`) until one succeeds. The program's flags then say which (`+0x34`: bits 1 and 2 binary, 1 and
  4 intermediate, 1 and 3 source) and its set-up bit is cleared with a store barrier. The bind (`f_814f2010`) takes the
  made one and binds it for every stage.
- **nn::gfx's initialize** (`f_812c42c0`) makes the NVN program three ways: `nvnProgramInitialize` with
  `nvnProgramSetShaders` (16 bytes a stage: code address, control section), with `nvnProgramSetShadersExt` (24 bytes a
  stage), or through a program builder (`nvnProgramBuilderSetShaders`, `nvnProgramInitializeFromBuilder`). It may copy the
  code into a memory pool of its own first, and asks the device four integers. Which way it takes for OoT's files was not
  worked out. **read** (the three ways)
- **agl programs** (`f_806569a0`): each agl program keeps a dirty word (`+0x10`). Its update (`f_80654d70`) runs when the
  word has bits set: bit 0 makes the NVN program again (the old one finalized first, under a lock),
  the others look up its attributes, uniforms, blocks and samplers again by name. Making it, the program looks in each
  stage's reflection for a uniform block named `RegisterUBO` (else `globalParams`) and keeps its slot per stage: the block
  that carries the program's loose uniforms. The program is named `<archive>.<program>[<variation>]` (`%s[%d]`).
  **read**
- **The middleware's programs** (NDSR, NDVE) are made with program builders, three each (`f_80f71a00`, `f_82105e20`).

Programs are finalized with `nvnProgramFinalize` (9 places: `f_812c4f00`, `f_806569a0`'s remake, `f_8098a9f0`, the
middleware's). **read**

### Binding per draw

The model draws' bind is on [drawing.md](drawing.md): `f_8008fc00` binds `BindProgram` only when the material's program
differs from the one the draw context holds, and every batch starts with an empty context. What reaches NVN is
`nvnCommandBufferBindProgram` (212 places, [nvn-calls.md](nvn-calls.md)).

agl's passes bind their own way, all through four functions that most passes inline-check first:

- `f_802a1760` (rank 841, 202 callers): the program. It takes the dirty word (an atomic set of bit 31), runs the update
  if it had bits, and binds the program (stage mask `0x20` or `0xff` by the context's `+0x183`). Then it binds the
  `RegisterUBO` block to each stage that has one. A thread that finds bit 31 already set sleeps and checks again until
  the other thread is done.
- `f_8029f590` (rank 846) and `f_80296c30` (rank 1111, 545 callers): a uniform or storage block's slice, bound to each
  stage whose byte in the location word is not `0xff` (`nvnCommandBufferBindUniformBuffer` and `BindStorageBuffer`, or
  the inline path `f_8353e5d0`).
- `f_802a67c0` (rank 627): a sampler location: the texture handle (`nvnDeviceGetTextureHandle`) and a
  `BindTexture` per stage.

### Shader resources

- **Uniform blocks** are GPU buffers bound by address; per-frame data goes through agl's dynamic blocks, a buffer made
  over a memory pool at start-up (`f_80c4acc0`, from `f_80c4a1c0`, which also makes
  `agl::utl::DynamicUniformBlockAllocator` and `agl::utl::DynamicShaderStorageBlockAllocator`). **read**
- **Storage buffers** carry the models' geometry: model draws bind no vertex buffers ([drawing.md](drawing.md)). **read**
  (there)
- **Textures and samplers** are bound as handles from the device (`GetTextureHandle`), the pools set once per command
  buffer (`SetTexturePool`, `SetSamplerPool`: `f_80665980`, `f_802eb390`). **read**
- **Scratch memory**: `nvnCommandBufferSetShaderScratchMemory` is called three times: at the frame's first command
  buffer (`f_80665980`), in `f_802eb390` (rank 3675) and for the movie upscaler's output (`f_83b83360`). The first takes
  the pool and size from the graphics object (`[0x84e9f6c8]`, `+0xa8` and `+0xb0`), when its flag allows. The game never
  asks NVN how much scratch a program needs (`nvnProgramGetScratchBytesPerWarp` has no caller). **read**
- **Render settings:** the only shader-related settings among the render pipeline's are the meshlet debug views
  (`visualize_mode_meshlet_id`, `…_aabb`, `…_cone_cutoff`, in `f_811cc790`, [culling.md](culling.md)).
  `gsys_shader_option`, `gsys_override_shader` and `gsys_shader_debug_log` are strings no code refers to. **read**

### Compute, task and mesh programs

- **Compute**: agl's passes dispatch their programs after `f_802a1760` (`f_80364080`, rank 79, on
  [drawing.md](drawing.md), is the hottest), the particles from `f_81260fc0` ([effects.md](effects.md)), the foliage
  indirectly (`f_808833e0`), NDSR's from `f_81503720` (rank 1776, with push constants: the hottest user of
  `nvnCommandBufferPushComputeConstants`). **read**
- **Task and mesh**: meshlet shapes draw with `DrawMeshTasks` ([drawing.md](drawing.md)); the task shader that culls
  meshlets against a coarse depth grid is code key `2c56dc1006942f74` ([culling.md](culling.md),
  [rendering.md](rendering.md)). Which task shader a material gets follows its `gsys_meshlet_process_type_*` option:
  passthrough, or one that uses the prepass. **read** (the option), **guess** (that "use prepass" is the culling one)
- **The networks**: the TAAU and the movie upscaler are compute programs with tensor-core matrix multiplies (HMMA)
  (`docs/design.md`); LS's census finds networks in agl's NDSR packs and in NDVE. **read** (there),
  **guess** (which network is in which)

### The terrain's programs

The terrain's agl programs are set up by `f_80b6c8a0` from `tera_common`, `tera_develop` and `tera_debug`, with
uniform and sampler names such as `cBrushInfo`, `cMaterialBakeParam`, `cTexMaterialBake` and `cTexPasteHeight`: the
GPU paints and bakes the terrain's tiles ([textures.md](textures.md)). Its model shading uses the names `f_80942d20`
gives (on [textures.md](textures.md) as `Tera_Programs`): `tera_material`, `tera_normal`, `tera_height`,
`tera_height2`, `tera_bake_gi`, `tera_bake_ao`, `tera_custom`, and the passes `OpaqueNear`, `OpaqueMiddle`,
`OpaqueFar`, `ZOnly` and their `…Deformable` forms. Its render options are `tera_render_type` (`NormalNear`,
`NormalMiddle`, `NormalFar`, `DegeneracyX`, `DegeneracyY`), `tera_render_normal`, `tera_render_hiatus_x` / `_y`, and
`route_render_*` for paths (`f_83be2200`). **read** (the names)

## Functions

### Archives

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83b728f0` | `GraphicsModule_LoadShaderResources` | Under the name "grphics::GraphicsModule": opens `Lib/agl/agl_resource` and `Lib/gsys/gsys_resource` (`.Nin_Ounce_NVN2.release.sarc`), `Shader/ExternalBinaryString.bfres`, and for each name in a list of up to 32 (0x50 bytes each) formats `Shader/%s%s%s.%s.bfsha` with the name, `.Product` or `.All`, `.product` and `Nin_Ounce_NVN2`, then asks `f_801b0950` for it. No callers by BL (a vtable slot). Not ranked | read |
| `f_80c75b70` | `GsysMinimum_Load` | Loads `gsys_minimum.bfsha` and gsys's configuration (`gsys` `gmsconf`, `f_80c76250`), calls the package loader `f_80722da0` and sets up a shader archive (`f_80c760d0` → `f_8017dd60`). Not ranked | read |
| `f_8017dd60` | `GsysShaderArchive_Init` | Sets up a shader archive (`f_80796e40`), then its per-model data (`f_8017ed80`, 1,416 instructions). Called for a model's archive (`f_804566e0`), gsys's minimum one and by `f_83b71980` (engine::GraphicsModule) | guess |
| `f_80fd0d10` | `Layout2_LoadMergeShader` | Formats `Layout2Shader/MergeShader.%s.Nin_Ounce_NVN2.bfsha` and loads it through the UI's archive manager (`eui::ArcResourceMgr`, `f_80fd0f80`) | read |
| `f_80fd42f0` | `TerrainModule_LoadResource` | Under "terrain::TerrainModule": loads `Lib/Terrain/tera_resource.Nin_Ounce_NVN2.release.sarc` | read |
| `f_80f71360` | `Ndsr_LoadNetworks` | Loads `agl_ndsr.pck`, `agl_ndsr_vr.pck` and `agl_ndsr_vr_light.pck` with a `ndsr_tmp` work area, then sets up the library (`f_80f718a0` → `f_80f71a00`) | read |

### Options, keys and variations (nn::g3d)

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80796e40` | `ResShaderArchive_Setup(archive, device, mutexes)` | Sets the archive's program callback (`+0x28`) to `f_814f26c0`; for each shading model (`+0x40` of them, 256 bytes each at `+0x10`) makes its mutex (32 bytes each, at `+0xb0`), gives its code GPU addresses (`f_812c3d60` on the first block of the BNSH file at `+0xa8`) and marks every program (`+0xe8` of them at `+0x88`, 64 bytes each) as needing set-up (`+0x34` bit 0). When `+0x42` bit 1 is set it tries the first program's binary (`f_814f14c0`) and sets bit 3 if it loads; ends with `+0x42` bit 7. Not ranked | read |
| `f_814f26c0` | `ResShaderProgram_Setup(device, model, index)` | The archive's callback. Makes the program's shader from its variation (`+0x20`): the binary one at `+0x10` (when the archive's `+0x42` bit 3 is set), else the intermediate at `+0x8`, else the source at `0`, each with `f_812c42c0` on its `+0x68`; sets `+0x34` bits 6, 0x12 or 0xa by which; then `f_814f15c0` (the program's slot tables, when the shader container, the first block of the model's BNSH file at `+0xa8`, has `+0x10` at 1) and clears bit 0. Reached only through its address | read |
| `f_814f1f50` | `ResShaderProgram_SetupIfNeeded(program, device)` | When `+0x34` bit 0 is set, calls the archive's callback for this program, under the shading model's mutex when it has one | read |
| `f_814f2010` | `ResShaderProgram_Bind(program, cmd)` | Takes the shader `+0x34` says was made and binds it for all stages (`f_812bd9d0`, mask `0x1ff`). Callers in the UI (`f_8150a630`, rank 1008); `f_80444a90` makes the same choice and returns the shader. Hot rank 1406 | read |
| `f_814f25e0` | `ShadingModel_FindProgramIndex(model, keys, key)` | Binary search of the model's key table (each entry `+0xeb` static and `+0xea` dynamic words) for the static words of `key`; a table shorter than one entry is compared directly. Returns the index, or −1 when no entry matches | read |
| `f_814f20b0` | `ShaderOption_WriteKey(option, key, choice)` | Writes a choice into a key: the word `+0x24` less `+0x1f`, cleared by the mask `+0x20`, the choice shifted by `+0x25` | read |
| `f_814f25c0` | `ShaderKey_Clear(model, key)` | Fills a key's static words (`+0xeb` of them) with `0xff` | read |
| `f_8044f300` | `ShaderSelector_Init(sel, src, buffer, size)` | Lays out a selector's static, dynamic and last keys in a buffer when it is large enough; for a model with static options writes the default key (`f_814f23c0`) and clears the last key, so that the next update searches; for one without, finds the program at once | read |
| `f_80442f00` | `ShaderSelector_Update(sel, device)` | When the static key differs from the last one (`memcmp`, `+0xeb` × 4 bytes): finds the program (`f_814f25e0`), keeps the key and the program (`model+0x88 + index * 64`, at `sel+0x20`), and sets it up if needed (`f_814f1f50`). Not found: clears the last key and selects nothing. Returns whether one was found | read |
| `f_80444a50` | `ShaderSelector_WriteChoice(sel, option, choice)` | Writes a choice of option *n* (40-byte records at `model+0x18`) into the static key (`sel+8`) or, for a dynamic option (`+0x1e` bit 0), the dynamic key (`sel+0x10`) | read |
| `f_80444ae0` | `ShaderSelector_WriteDefaults(sel)` | Writes each option's default choice (`+0x1a`) into its key (`model+0xe6` options) | read |

### Materials

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80443000` | `GsysMaterial_SelectProgram` | A material's program: collects the option and choice pairs (above), finds each in the model's dictionaries and writes it into the selector, then selects under a lock (`f_80442f00`). Also takes the meshlet process type from the shader object's `+0x22c` (its selector at `+0x90`). 1,684 instructions. Not ranked | read |
| `f_8044db80` | `GsysMaterial_Setup` | Names the material (`%s>%s>%d(%s%s)`), sets up its parameters (`f_8044f420`) and its program (`f_80443000`) | guess |
| `f_8044f420` | `GsysMaterial_SetupParams` | Reads the material's render state (`f_80451d60`, `f_80451460`), its assign type (`f_8044ed60`), its material id (`f_8044d470`) and names its lighting inputs (`gsys_area_env_data0`/`1`, `gsys_light_diffuse`, `gsys_light_specular`, `gsys_point_light_color`, `gsys_xlu_zprepass_alpha`, `enable_color_buffer`) | guess |
| `f_80451d60` | `GsysMaterial_ReadRenderState` | Reads `gsys_render_state_mode` (`opaque`, `mask`, `translucent`, `custom`), `gsys_render_state_display_face`, the depth test (`gsys_depth_test_enable`, `_write`, `_func`), the blend (`gsys_color_blend_*`, `gsys_render_state_blend_mode`) and the alpha test | read |
| `f_8155dc50` | `Layout2Material_BuildKey` | Builds a UI material's choices from its pane: `PaneType`, the texture count, `ShaderGraph_TextureBlendExpression`, the procedural shape, slicing and texture-mapping options (`o_*`, per texture 0 to 5) | read |
| `f_8155ee00` | `Layout2Material_SelectProgram` | Sets up the selector (`f_8044f300`), writes the choices (`f_8155dc50`) and selects (`f_80442f00`) | read |
| `f_8155e9c0` | `Layout2Material_Init` | Lays out a UI material's buffers (maps its buffer, `f_800a8df0`), then selects its program (`f_8155ee00`). Hot rank 965 | guess |

### Making programs (nn::gfx)

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_812c42c0` | `GfxShader_Initialize(shader, device, info)` | Makes an NVN program from a shader's stages (each found with `f_8017ed50`, stages converted with `f_812c3480`): `nvnProgramInitialize` then `SetShaders` or `SetShadersExt`, or a program builder; may copy the code into a memory pool of its own (`nvnMemoryPoolInitialize`, `GetBufferAddress`); asks `nvnDeviceGetInteger` four times. 784 instructions. Not ranked | read |
| `f_812c4f00` | `GfxShader_Finalize(shader)` | `nvnProgramFinalize` on the program (`+8`), then its own memory pool (`nvnMemoryPoolFinalize`) and buffers | read |
| `f_812c3d60` | `GfxShaderContainer_Initialize(container, device, pool, offset)` | For each variation (`+0x1c` of them, 64 bytes each at `+0x20`) and stage, turns the code's offset into a GPU address in the pool that holds the file (`nvnMemoryPoolGetBufferAddress`, or an internal pool, `f_80792e30`) | read |
| `f_814f14c0` | `ResShaderProgram_TryBinary` | Makes one shader (`f_812c42c0`) and finalizes it again (`f_812c4f00`): whether the archive's binaries load | read |

### agl programs

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80654d70` | `AglShaderProgram_Update(program, flags)` | Bit 0: make the program again (`f_806569a0`). The other bits each look up one list of 16-byte locations again by name (counts at `+0x28`, `+0x38`, `+0x48`, `+0x58`, `+0x68`, `+0x78`). Inline-checked at 420 places in 95 functions, 46 of them ranked (`f_80364080`, rank 79, the hottest). Not ranked itself | read |
| `f_806569a0` | `AglShaderProgram_MakeProgram(program)` | Names it `%s[%d]`, finalizes the old NVN program when it had one (under the lock at `+0x4c0` of `g_GfxSystem`, [drawing.md](drawing.md); `f_8353a610`), `nvnProgramInitialize` on `+0xc0` with the device (`[0x84e9f6c8]+0x170`), finds each stage's `RegisterUBO` (else `globalParams`) block by name (`f_80656260`, which knows arrays) and keeps its size (`+0x18`) and slots (`+0x1c`…`+0x1f`), then `nvnProgramSetShaders` and `+0x20` bit 0 | read |
| `f_802a1760` | `AglShaderProgram_Bind(program, ctx)` | Takes the dirty word (`+0x10`, bit 31 as a lock), updates the program if it changed, sleeping while another thread holds it; binds it (`nvnCommandBufferBindProgram`, `+0xc0`) and its `RegisterUBO` per stage. Hot rank 841, 202 callers | read |
| `f_8029f590` | `AglUniformBlock_Bind(block, ctx, location, index)` | Binds a slice (`index` × the aligned size) of a block's buffer to each stage whose byte in `location` is not `0xff`. Hot rank 846, 96 callers | read |
| `f_80296c30` | `AglBufferBlock_Bind(block, ctx, location, index)` | The same for a block that is a uniform or a storage buffer by its kind (`BindUniformBuffer`, `BindStorageBuffer`). Hot rank 1111, 545 callers | read |
| `f_802a67c0` | `AglSampler_Bind(…)` | Updates the program if it changed, then `nvnDeviceGetTextureHandle` and `BindTexture` per stage. Hot rank 627, 111 callers | read |
| `f_80b72180` | `AglShaderHolder_Init` | "agl::detail::ShaderHolder": agl's own programs from its `sharc`/`sharcb` (`Common`), with their uniform and sampler names (`uProjViewWorld[0]`, `uColorMatrix[0]`, `cNV12DecodeParam1`, `uCasParam[0]`, …). 10,256 instructions | read |
| `f_80b7cc30` | `GsysUtilPrograms_Init` | gsys's agl programs: `common`, `g_buffer` (`g_buffer_clear_header`, `g_buffer_expand_material_id`, `g_buffer_fill_infinity_depth`, `clear_geometry_culling`, `analyze_miplevel`, …) | read |
| `f_80b6c8a0` | `TeraPrograms_Init` | The terrain's agl programs (`tera_common`, `tera_develop`, `tera_debug`) and their names | read |
| `f_80b696d0` | `AppPackagePrograms_Init` | The application package's `AglShader` and `FoliageShader` archives | guess |

### Middleware and frame set-up

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80f71a00` | `NdsrSmaa_Init` | Under "SDK MW+Nintendo+SMAA_For_NDSR-2_3_2-Release": a memory pool, samplers and three program sets made with program builders (`f_815023d0`, `f_81503280`, `f_81503ce0`) | read |
| `f_81503720` | `NdsrSmaa_Dispatch` | Binds a program, images, textures, storage and uniform buffers, pushes compute constants, and dispatches (direct and indirect), with barriers and a buffer clear. Called from `f_802722b0` (rank 2050). Hot rank 1776 | read |
| `f_82105e20` | `Ndve_MakeResources` | A memory pool, a sampler, a texture and programs made with a builder (`f_821066c0`); next to `f_821057e0`, which the NDVE output (`f_83b83360`, [rendering.md](rendering.md)) calls | guess |
| `f_80c4acc0` | `DynamicUniformBlock_Init` | "agl::detail::DynamicUniformBlock": an NVN buffer over its memory (`nvnBufferInitialize`, `GetAddress`), made by `f_80c4a1c0` | read |
| `f_802eb390` | `DrawTree_SetupCommands` | A command buffer of its own: the memories, `BeginRecording`, an event signal and wait, a timestamp, `SetSamplerPool`, `SetTexturePool`, `SetShaderScratchMemory`, `EndRecording`, then `QueueSubmitCommands`. From `f_802eb020` ([jobs.md](jobs.md)). Hot rank 3675 | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84cf0008` | `ShaderVariation_KindOffsets` | Three offsets in a shader variation, in the order tried: `0x10` (binary), `0x8` (intermediate), `0` (source); read through `[0x84cad160]` | read |
| `0x83e7a9f0` | `GfxShaderProgram_StageOffsets` | Eight offsets (`8` to `0x40`) of a program's stage records, by nn::gfx's stage (read by `f_8017ed50`) | read |
| `0x84d63100` | `g_AglRegisterUboName` | Points at "RegisterUBO", the uniform block `f_806569a0` looks for first | read |
| `0x84e9f6c8` | `g_GfxDevice` | The graphics object (through the GOT slot `0x84cacc98`): `+0x170` the NVN device, `+0x1a0` the sampler pool and `+0x180` the texture pool, `+0xa8` and `+0xb0` the shader scratch memory's pool and size | read |

## Structures

### A shading model (256 bytes, at `archive+0x10`)

| Offset | Field | Sure |
|---|---|---|
| `+0x18` | The options, 40 bytes each | read |
| `+0x88` | The programs, 64 bytes each | read |
| `+0x90` | The key table | read |
| `+0x98` | The archive (its `+0x28` the set-up callback, `+0x42` flags) | read |
| `+0xa8` | The BNSH file (its first block the shader container) | read |
| `+0xb0` | The mutex set-up takes | read |
| `+0xe6` | The number of options (16-bit) | read |
| `+0xe8` | The number of programs (16-bit) | read |
| `+0xea` | The dynamic key's words (8-bit) | read |
| `+0xeb` | The static key's words (8-bit) | read |

### An option (40 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0x1a` | The default choice (16-bit) | read |
| `+0x1e` | Bit 0: a dynamic option | read |
| `+0x1f` | The first word of its key | read |
| `+0x20` | Its mask in the word | read |
| `+0x24` | Its word in the whole key | read |
| `+0x25` | Its shift in the word | read |

### A program (64 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0x20` | The variation (its programs at `+0x10`, `+0x8`, `0`; each one's `+0x68` the shader) | read |
| `+0x28` | The shading model | read |
| `+0x34` | Flags: bit 0 needs set-up; bit 1 made; bit 2, 4 or 3: the binary, intermediate or source one | read |

### A selector

| Offset | Field | Sure |
|---|---|---|
| `+0` | The shading model's holder | read |
| `+8` | The static key | read |
| `+0x10` | The dynamic key | read |
| `+0x18` | The last key looked up | read |
| `+0x20` | The selected program, or 0 | read |

### An agl program

| Offset | Field | Sure |
|---|---|---|
| `+0x10` | The dirty word: bits of what to update; bit 31 while a thread updates it | read |
| `+0x18` | The `RegisterUBO` block's size | read |
| `+0x1c` … `+0x1f` | Its slot in each stage (`0xff`: none) | read |
| `+0x20` | Bit 0: the NVN program is made | read |
| `+0x22` | The variation's index, in its name (16-bit) | read |
| `+0xc0` | The NVN program | read |

## Part 2: what the port does with them

Read from LS's `main` (c67cd4a0, 13 November 2026). The design notes and the development guide have the switches
in full (`docs/design.md`, `docs/development.md`); this is the shader path through them.

### The path, end to end

1. **The game makes a program.** `nvnProgramSetShaders` / `SetShadersExt` (and the builder's) land in
   `set_shaders` (host/nvn_native.c): each stage's code address (GPU) and control section (guest memory) are kept, the
   program gets a serial, and `rx_nvn_program_created` tells the back end, which (with `RX_VK_LOAD=create`) starts
   loading what it kept for those stages (`vk_programs.cpp`, `program_life.h`: Created → Loading → Ready → Released).
   Nothing is translated here. `nvnProgramFinalize` releases it. `SetShaderScratchMemory` and
   `nvnDeviceSetIntermediateShaderCache` do nothing.
2. **The first draw or dispatch asks for a variant** of the bound program (`program_pick`, vk_shader.cpp). A stage is
   read as the game has it (`read_code`): the 0xd8-byte control section (magic `0x98761234`, code length `+0x38`, c1
   constants `+0x40`…`+0x44`, stage `+0x56`, workgroup `+0x58`) and its code area (a graphics stage's 128-byte header,
   the Ampere instructions, the c1 constants). Its **code key** is a hash of the two (`code_key`).
3. **SM86 to Maxwell.** `LowerToMaxwell` (`eden/shader_recompiler/frontend/ampere`) lowers the Ampere instructions;
   HMMA becomes a cooperative-matrix multiply where the GPU has them (`gpu/vk/vk_coopmat.cpp`).
4. **Maxwell to IR.** Eden's Maxwell front end translates the program, its questions answered from the NVN state: c0
   and c2 from the driver's banks (`gpu/vk/vk_bank.cpp`), c1 from the code area, the user
   uniform buffers (c3–c18, slots 0–15) from guest memory, texture types from the registered textures. The IR passes
   (`ir_opt`) then run: global memory to storage buffers, the fragment output NaN guard, the HDR tone map.
5. **IR to SPIR-V** (`backend/spirv`, written with sirit), one `VkShaderModule` a stage (`make_module`). A program's
   stages share one descriptor set in Eden's order. The pipeline is made in `gpu/vk/vk_draw.cpp`
   from the variant and the draw state.

**Two meanings of "variant".** The game's variations are chosen by option keys before the renderer sees anything
(Part 1). The renderer's variants of one set of stages differ by what the translation had to be told: the bound
textures' types (integer or float, and the image's kind: the R32UI views of R11G11B10 targets once turned the scene
yellow), the vertex attributes' types and the colour targets' types (`Program::answers`, `variant`, vk_shader.h).

### The program cache and its stamp

- **`longshot_programs.bin`** (and `longshot_programs.idx`) beside the game: the SPIR-V and `Shader::Info` of every
  variant translated, by key (`vk_progcache.cpp`; `RX_VK_PROGRAM_CACHE_ZSTD=1` writes `longshot_programs.zst.bin`).
- **What a record is good for** is `program_cache_stamp`: the translator's stamp (`translator_stamp`, vk_shader.cpp) and
  the cache's own format (`kProgramCacheFormat`). The translator's stamp hashes `LONGSHOT_SHADER_STAMP` (nx2/build.py's
  hash of the translator's sources), the depth mode, the GPU's mesh path, its warp lanes and storage alignment, f16
  support, the C++ library's layouts, the vendor fixes, the float-add path, and every environment switch the
  translation reads (`RX_VK_MESH*`, `RX_VK_NATIVE*`, `RX_VK_NAN*`, `EDEN_*` and the others listed there). A change to any
  translates every program again. (`disk_stamp` in HS's vk_shader.cpp is the older form:
  `tests/test_program_cache_format.py` checks that LS has no second stamp.)
- **`mkw_vk_pipelines.bin`** is the driver's pipeline cache, dropped when `mkw_vk_pipelines.stamp` names another
  translator (`RX_VK_PCACHE_STAMP=0` keeps it). **`longshot_pipelines.bin`** notes every pipeline play made, for the
  precompile.

### Preparing before the title

- **`longshot_shaders.bin`** (`HKSEED01`) is written by the builder from the player's own romfs
  (`nx2/shader_census.py`): every BNSH variation, BAHS program, NDSR and NDVE network and
  particle set, as its control sections and code. Nothing of it goes into the repository.
- **The preparation** (`seed_open`, `seed_program`, `seed_compute`, vk_shader.cpp) translates each for the default state
  (nothing bound, float inputs and outputs; vertex programs that read attributes 6 and 7 also with integer inputs, for
  the skinned models' bone indices), and each compute program with every texture answer its questions can take. Then it
  builds the noted and listed pipelines. It runs before the title in release builds (`RX_RELEASE=1`), with
  `RX_VK_PRECOMPILE=1` in any build, or alone with `--prepare-shaders`; `RX_VK_COMPILE_SHADERS` (`most`, `all`) picks what
  it builds.
- **The pipeline state lists** (`longshot_pipeline_states_shipped.bin`, merged from `assets/pipeline_states/`; play adds
  to `longshot_pipeline_states.bin`, compute programs to `longshot_computes.bin`) name pipelines by their shaders'
  hashes and state, so a player's build turns them back into pipelines from their own `longshot_shaders.bin`
  (`nx2/pipeline_states.py`, `docs/development.md`).
- **When loads happen** (`RX_VK_LOAD=create` or `start`): at the game's program create call (point 1 above) or all
  before the title. Since the game makes many programs during play (Part 1, *In short* 1), `create` is the mode that
  depends on it.

### Task and mesh programs

`translate_mesh_into` (vk_shader.cpp) translates a task and mesh pair with the mesh registers the Switch 2 driver would
set (`mesh_registers_of`). Where the GPU has `VK_EXT_mesh_shader` they become task and mesh stages; otherwise, or where
chosen, the **compute fallback** runs them as compute stages pinned to 32 lanes, writing records a generated vertex
stage then draws. `RX_VK_MESH_SHADERS=native` or `compute` chooses (`mesh_mode.h`); unset, the fallback is used on GPUs
without mesh shaders and on Linux AMD (RADV, issue 2245), and for task + mesh draws on Navi 21 and 22 under Windows
(`RX_VK_TASK_MESH_FALLBACK`, issue 996). The stamp holds the mesh path, so a change translates everything again.

### Per-shader fixes

The programs the draw path recognises by code key (the lighting composite, the TAAU network, the tone map, the
meshlet culling and others) are on [rendering.md](rendering.md#what-the-renderer-recognises). In the translation itself
(vk_shader.cpp):

| What | Key or test | Fix |
|---|---|---|
| The meshlet culling task shader | `RX_VK_MESH_NOCULL=<prefixes>` (`2c56dc1006942f74` on the Steam Deck) | Its answer becomes "visible" (`visible = (result & 1) \| (c3[2320] & 4)` made true), and dead code takes the grid walk: every meshlet drawn (issue 817) |
| The Deku Tree's web | vertex code `b449bcdb69b36a8a`, NVIDIA only | Its 128-bit global loads inlined: NVIDIA's compiler took the grid origin for its size (#455) |
| The networks (cooperative matrices) | `RX_VK_NETWORK_KEEP` (default `fd029a317a9a4b94,9dc98871eb7fc101`) | Others are left out where the matrices are software or the GPU is not NVIDIA's (the movie's last pass becomes a plain upscale); `RX_VK_NATIVE_MMA_KEYS` picks which use the GPU's own matrices |
| Water written as noperspective (the Royal Family's Tomb) | any fragment stage with noperspective inputs | Written times w and interpolated perspective-correct (AMD's depth, #1830) |
| Float colour outputs | every fragment stage | Kept finite (`RX_VK_NAN_GUARD=0`: off): one NaN through the bloom was a white flash |
| The tone map | a fragment stage with the ACES-fit constants | The HDR tone mapper (#2481) |

The translation's **vendor workarounds** (`vk_vendor.h`, each chosen from the device and forced with its switch):
`RX_VK_FETCH_BOUNDS` (out-of-bounds texel fetches answered with 0 on every GPU but NVIDIA's: the ground fog's edges on
AMD), `RX_VK_NAN_BITS` (NaN tests on a value's bits on AMD's older Windows compiler, RX 5000 and 6000),
`RX_VK_GATHER_OFFSET` (gathers nudged by 1/512 of a texel on AMD and Intel: dashed shadow edges), and
`RX_VK_NATIVE_FADD` (float adds to storage buffers as the GPU's atomic, by default on NVIDIA only, else a
compare-exchange loop that gives up after 1024 tries).

### Switches

| Switch | What it does |
|---|---|
| `RX_VK_PRECOMPILE=0` or `1`; `--prepare-shaders`; `RX_VK_COMPILE_SHADERS=most` or `all` | The preparation before the title off or on; a preparation alone; what it builds |
| `RX_VK_LOAD=create` or `start` | When kept programs and pipelines are loaded |
| `RX_VK_NO_PROGRAM_CACHE` | Neither reads nor writes the program cache |
| `RX_VK_MESH_SHADERS=native` or `compute`, `RX_VK_TASK_MESH_FALLBACK=0` or `1` | The mesh path |
| `RX_VK_MESH_NOCULL=<code key prefixes>` | The task shaders' culling off |
| `RX_VK_PIPE_SKIP=0`, `1`, `2` | What a draw does while its translation or pipeline is not ready: wait for both (0), wait for the translation (default), or leave out untranslated draws on persistent targets at once (2) |
| `RX_VK_TRANSLATE_WAIT_MS=<ms>` | Off by default; set (8 tested), a recording thread waits at most that long a frame for translations, then leaves the draws on earlier frames' targets out (`translate_wait.h`) |
| `RX_VK_SKIP_PROG=<prefixes>`, `RX_VK_SKIP_CODE` | Leave out the draws of these programs (default `53509044401d78d2`, the foliage's noise volumes) |
| `RX_VK_REJECT_KEYS`, `RX_VK_REJECT_STAGES`, `RX_VK_LOWER_SKIP` | Take programs as untranslatable, for bisecting |
| `RX_VK_DRIVER_FAULTS=0` or `2` | Programs the driver's compiler faulted on asked again (0), or built one at a time on every driver (2); kept in `longshot_driver_faults.txt` |
| `RX_VK_DUMP_STAGES=<folder>` | Each stage's control section and code as the game has it, as `<code key>.bin` (game data: keep it local) |
| `RX_VK_DUMP_NATIVE`, `RX_VK_DUMP_FAILED`, `RX_VK_IR`, `RX_VK_DUMP_SPV`, `HOS_SHADER_DUMP` | The native code, the programs that fail, the IR and the SPIR-V of chosen programs, the stages as the host received them |
| `RX_VK_LOG_PROGRAM_LIFE=1` | A log line at each program the game makes or frees and at its first use |

### Files

| File (beside the game) | What it is |
|---|---|
| `longshot_shaders.bin` | Every stage in the game's archives, from the player's romfs (the seed) |
| `longshot_programs.bin`, `.idx` | The translated variants (SPIR-V and info), by key |
| `mkw_vk_pipelines.bin`, `.stamp` | The driver's pipeline cache, and which translator made it |
| `longshot_pipelines.bin` | The pipelines this install's play made, for the precompile |
| `longshot_pipeline_states_shipped.bin`, `longshot_pipeline_states.bin`, `longshot_computes.bin` | The shipped and played pipeline lists |
| `longshot_module_ids.bin`, `longshot_driver_faults.txt`, `longshot_load_start.txt` | Shader module identifiers, faulted programs, the load mode's marker |

### What can go wrong

- **First-visit pauses.** A program met for the first time with no kept variant is translated and its pipeline built
  in play: 0.2 to 3 s on a cold or changed cache. By default the draw waits (#1573: a memory-writing draw left out
  lost what it wrote: invisible doors and Kaepora Gaebora on v2.1.0); `RX_VK_TRANSLATE_WAIT_MS` bounds the wait at the
  cost of those writes. Since the game makes programs during play (Part 1), a release that prepares everything is what
  keeps play smooth.
- **The preparation itself** (worklist score 3.2): builders stuck at "Preparing shaders" or at 99%, one failed program
  stopping it, stutter after an update when it did not run. Fixes since v2.1.0: progress with the time left (#2186), a
  driver that stops answering (#2195), fewer predicted pipelines (#2323); open #2465, #2474, #2586, #2625.
- **Drivers' compilers.** AMD on Windows: device loss and freezes (worklist 2.9); a faulted program is kept out (#1823),
  Navi 21's task + mesh draws go through the fallback (#2215). Linux AMD's native mesh path deadlocked the GPU (#2362:
  the fallback by default). Intel UHD crashed in `igvk64.dll` while pipelines were built (#2456 open). RADV on GCN cards
  did not finish the two largest mesh fallback stages (`RX_VK_FALLBACK_PHI_LIMIT`). An RX 580 draws hair white (f16
  lowered, #2343, to retest).
- **Translation bugs show as wrong pictures**, not crashes: a texture type baked into a variant (the yellow scene),
  noperspective water (the red line), NaNs (white flashes), the gathers (dashed shadows). Each was a per-shader or
  per-vendor fix above, keyed by code.

## Notes

- **Why the code key is the right handle.** The game chooses among thousands of variations by option keys the renderer
  never sees, but every variation it makes is a set of stages with fixed code, and that code is what `nvnProgramSetShaders`
  hands over. So a fix for "the water in one room" is a fix for one code key, whichever material chose it. **read**
- **The agl sleep.** `f_802a1760`'s wait is a loop of a clock read (`f_80e5a5c0`) and a sleep (`f_809667e0`). It only
  runs when two threads bind the same agl program while it is being updated, which with the stage slot jobs
  ([drawing.md](drawing.md)) recording passes side by side is possible on the frame a program changes. **read** (the
  loop), **guess** (that it happens in play)
- **The three NVN ways.** nvn-calls.md counts `nvnProgramSetShaders` at 2 places (`f_806569a0`, `f_812c42c0`),
  `SetShadersExt` at 1 (`f_812c42c0`) and the builder at 7 (`f_812c42c0`, NDSR's three, NDVE's three). Under LS
  all of them end in the same `set_shaders`, and a builder's program in `nvnProgramInitializeFromBuilder`. **read**

## Open questions

- Which of `f_812c42c0`'s three ways OoT's g3d archives take, and why it asks `nvnDeviceGetInteger` four times.
- The names in `f_83b728f0`'s list of up to 32 archives (they are data, not code), and what `.product` and
  `ExternalBinaryString.bfres` are for.
- How often programs are made during play: a count of `rx_nvn_program_created` per minute in a field, a town and a
  dungeon (`RX_VK_LOG_PROGRAM_LIFE=1`) would say how much `RX_VK_LOAD=create` and the first-visit waits depend on it.
- Whether the agl sleep in `f_802a1760` ever runs in play, and for how long.
- What the material shader object's `+0x22c` values mean (1 and 3 pick the two meshlet process types; 0 and 2 take
  other paths), and whether "use prepass" is the culling task shader `2c56dc1006942f74`.
- How the dynamic keys are filled (no writer of the dynamic key but the defaults was followed), and whether any model
  changes its program per frame.
- `f_814f15c0` (the program's slot tables after set-up) and `f_8017ed80` (gsys's per-model archive data) were not read.
