# The render pipeline

The game's frame on the GPU: which passes its pipeline has and in what order they run, the render targets each one
draws into (by the names the game gives them), and what LS's Vulkan renderer knows, assumes or works around for
each. Written so a rendering report (a missing wall, a flash, a wrong colour, an AMD-only artefact) can be traced from
what the renderer sees back to the game's own pass. Looked at in November 2026.

**LS code:** `gpu/vk/vk_draw.cpp` (passes, draws, the per-program workarounds),
`gpu/vk/vk_temporal.cpp` and `gpu/vk/vk_fsr3_inputs.cpp`
(the scene's camera, colour and depth, found for DLSS and FSR), `gpu/vk/vk_shader.cpp`
(per-shader fixes), `gpu/vk/vk_dump.cpp` (frame dumps)
**See also:** [culling.md](culling.md) (the z-prepass queries), [textures.md](textures.md) (the terrain's tiles),
[graphics.md](graphics.md) (resolution, the render-target pool), `docs/design.md`

## In short

1. **The pipeline is configured, not hard-wired.** About 340 parameters set it up (the settings sync in
   [culling.md](culling.md), and the parameter readers `f_835867c0` / `f_835927d0`): a switch per pass (`Create*`), each
   pass's sizes and formats, and the scene's limits. **read**
2. **The game gives the GPU no names.** It calls none of NVN's debug groups or markers, so a frame capture shows unnamed
   draws. The names exist only on the CPU: the draw types (`Model(GBuffer/Opa+AlphaMask)` and about 70 others) and the
   names it gives its render targets. **read**
3. **About 140 render targets have names.** Every temporary target comes from one allocator (`f_8005ed00`,
   agl::utl::DynamicTextureAllocator, [graphics.md](graphics.md)), which is given a name: `ssao_work`, `reflection_color`,
   `gsys::RenderBuffer::motion_buffer`, `Single Scattering Mie`. That list is the best map from the game's passes to the
   textures the renderer sees. **read**
4. **The renderer's "passes" are not the game's.** NVN has no render passes; the renderer opens a Vulkan rendering span
   itself whenever the targets change, a clear follows draws, or a draw samples what its own span wrote. So it counts
   about 150 a frame in Kokiri Forest, more than the game's list. **read**
5. **One traced frame (below) gives the order.** In Kokiri Forest the game shades its opaque scene forward, into one
   colour target: no multi-target G-buffer is drawn, although the pipeline has one. **tested**

## The pipeline's switches

The `Create*` parameters turn passes on, in the order the game lists them: light map, light probes, snapshot, depth shadow,
masked light, static depth shadow, environment cube map, reflection, z-prepass (`ZPrePass`), G-buffer (`GBuffer`), GPU
geometry culling for the z-prepass, light pre-pass, shadow pre-pass, forward soft edge, decals, SSAO (`CreateSsocclusion`),
occlusion queries (three: [culling.md](culling.md)), deferred opaque alpha mask, GPU geometry culling for the G-buffer,
outline silhouette, volume mask, shadow mask, radial blur, motion blur, depth of field, translucent z-prepass
(`CreateXluZPrePass`), bloom, occluded effect, auto exposure, HDR, colour correction, DLSS, anti-aliasing (`CreateFilterAa`)
and NDSR (Nintendo's upscaler). The rest of the parameters tune them: the depth shadow's size, cascades (up to four, with
near and far planes) and bias; the static shadow's map type, size and format; the G-buffer's formats (sRGB albedo, 8-bit
normals, the material id's format); reduced (half and quarter) buffers; water at half resolution; cube maps; decal AO; the
light, lens-flare, model and occlusion-query limits; and the debug visualise modes.

## A traced frame

One gameplay frame in Kokiri Forest (the first save, standing still; present 4,000; RTX 3080; the scene at 1920 x 864 in a
2560 x 1152 picture, shown in a 3440 x 1440 window), with `RX_VK_PASS_TRACE=1 RX_VK_PASS_TRACE_AT=1000` and
`RX_VK_DUMP=4000 RX_VK_DUMP_W=1024`: 267 of the renderer's spans and dispatches, 230 dumped pictures. Formats are Vulkan's:
B10G11R11 (122), D32 (126), D16 (124), RGBA16F (97), R32F (100), RGBA8 (37), RGBA8 sRGB (43). **tested**

| # | What the renderer draws | The game's pass | Sure |
|---|---|---|---|
| 1 | 256 x 144 depth only, 30 draws | The occluder depth for the occlusion culling (`OcclusionDepthBuffer`, `RenderOcclusionViewZ`); it sits at the start of the render-target pool, where the scene's depth goes next | read |
| 2 | Two 256 x 256 RGBA16F, one draw each | The sky's tables (the renderer never evicts this 256 x 256 LUT: the rainbow sky) | guess |
| 3 | 128 x 128 colour and depth, 253 draws; then 1 draw a level down to 1 x 1, twice | An environment cube map face and its filtering (`render_cubemap_color`, `cubemap_filter`) | guess |
| 4 | Four 480 x 216 depth-only spans and a 480 x 216 RG32F | Not identified | |
| 5 | 1920 x 864 depth with colour writes off, ~490 draws in spans of up to 120, with GPU culling dispatches between | The z-prepass (`Model(ZPrePass)`) with its 491 occlusion reports ([culling.md](culling.md)) | tested |
| 6 | ~60 compute dispatches, then ~10 one-draw spans and a second scene depth (15 draws) | GPU culling, lighting and material compute (`4b03b304` among them); the rest not identified | guess |
| 7 | 1920 x 864 R32F, 960 x 432 (three colour targets), 480 x 216 R32F; then four 480 x 216 spans (R32F, R16F, RG16F, RGBA32F) | Linear depth and its half and quarter reductions (`HalfNld`), then quarter-size passes (SSAO, the shadow and light pre-passes) | guess |
| 8 | 1600 x 1600 D16, three layers, 1-65 draws each | The sun's cascaded shadow map (three cascades) | read |
| 9 | 796 x 796 and 384 x 384 depth maps, each projected into a 1920 x 864 RGBA8 mask by program `1e26755d` | The characters' own shadows, and the shadow mask (Link's shadow and the foliage's sun shadow in separate channels) | tested |
| 10 | Compute at quarter size (60 x 14 to 60 x 54 groups) and over 8 x 9 x 12 and 16 x 9 x 12 grids | The froxel fog's volumes | read |
| 11 | 960 x 432 RGBA16F and depth (14 draws), two-target 960 x 432 spans, a 120 x 54 R8 target | Half-size passes: not identified (water, translucent or reflection candidates) | guess |
| 12 | 1920 x 864 B10G11R11 with the scene depth, ~455 draws, then single-draw spans | **The opaque scene, shaded forward into one colour target.** No multi-target G-buffer (`GBuffer(Albedo)` and the rest) is drawn in this scene | tested |
| 13 | One full-screen draw, program `1a7482c0` | The lighting composite: adds the fog and the atmosphere's haze | tested |
| 14 | 1920 x 864, ~40 draws, and 960 x 432 spans | Translucent and effects after the composite | guess |
| 15 | 1200 x 540 down to 37 x 16, then up to 300 x 135 | Bloom (`bloom_ex`, `reduce`) | read |
| 16 | One full-screen draw, program `ab368194` | Tone mapping | read |
| 17 | Four dispatches ending in `fd029a31`, written into the 2560 x 1152 picture | The TAAU upscale network | tested |
| 18 | The HUD (18 draws into the 2560 x 1152 picture), then one draw into the 3440 x 1440 window, program `b7914ae6` | The HUD and the present | tested |

So in this scene the game shades forward: the `GBuffer` switch and draw types exist, but nothing writes several colour
targets at once. Deferred scenes elsewhere would show up as multi-target spans at the scene's size.

## Other places

Two more traced frames (`RX_VK_PASS_TRACE_AT=900`, with dumps from `RX_VK_DUMP_FILE`), on a LS build of 12 November
2026, reached with the debug tools' teleport: Hyrule Field at the Lost Woods bridge, inside the Deku Tree, and Lake Hylia (child
Link swimming). This build
draws the scene at the picture's full 2560 x 1152, so its half- and quarter-size passes are 1280 x 576 and 640 x 288.
**tested**

| | Kokiri Forest | Hyrule Field | Inside the Deku Tree | Lake Hylia |
|---|---|---|---|---|
| Spans and dispatches | 267 | ~306 | ~311 | ~372 |
| Compute dispatches | 114 | 156 | 96 | 169 |
| Scene-size spans with colour | 48 | 46 | 40 | 92 |
| Most colour targets at once | 3 (half size) | 2 (half size) | 6 (cube map filtering) | 3 (half size) |
| Sun cascades | 22 spans | 28 spans | none | 36 spans |
| Other shadow maps | 796 x 796, 384 x 384 (characters) | 2400 x 2400 x 3 (the grass around Link), 768 x 768 | 115 x 115 x 4 (local lights: **guess**) | 2400 x 2400 x 3, 947 x 947 |
| Unusual | | | 86 one-draw spans into an 80 x 33 RGBA8 target at the pool's start, one program (`abf896d5`, `bc3ae540`): per-object visibility tests into `OcclusionColorBuffer` (**guess**) | |
| Half-size (two RGBA16F targets and depth) | 9 draws | 5 spans | 2 spans | 180 draws: the water's half-size pass (`WaterResolutionHalf`; the reflection or the reduced translucent buffer, `Model(ReducedBuffer/Xlu)`: **guess**) |

**No scene draws a G-buffer.** In all four, nothing writes several colour targets at the scene's size, Lake Hylia's water
included: Ocarina of Time shades forward wherever this was looked at. Water adds work around the forward pass instead: twice
the scene-size colour spans, and a half-size two-target pass that grows from 9 draws to 180. **tested** (four scenes)

## Render targets by pass

Each row is a function that allocates targets from the pool, with the names it passes. **read** for the names; the
pass each belongs to is from the names (**guess** where marked).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_806790d0` | `RenderBuffer_Make` | Allocates `gsys::RenderBuffer::color` and `gsys::RenderBuffer::motion_buffer`: the scene's colour and the motion buffer | read |
| `f_80666d20` | `RenderBuffer_SoftEdgeDepth` | Allocates `gsys::RenderBuffer::SoftEdgeDepth` (forward soft edges); also copies textures (`nvnCommandBufferCopyTextureToTexture`) | read |
| `f_802a3700` | `RenderBuffer_ColorHalf` | Allocates `gsys::RenderBuffer::Color(half)` | read |
| `f_8027f6e0` | `RenderBuffer_ColorReduced` | Allocates `gsys::RenderBuffer::color(ReducedBuffer)` | read |
| `f_836c46f0` | `RenderBuffer_ColorQuarter` | Allocates `gsys::RenderBuffer::color(QuatReduceBuffer)` | read |
| `f_802707b0` | `RenderBuffer_ColorAA` | Allocates `gsys::RenderBuffer::color(AA)`: the anti-aliasing's input | read |
| `f_802a48b0` | `DepthBuffer_HalfNld` | Allocates `agl::utl::DepthBufferEx::HalfNld(Max)`: half-resolution linear depth | read |
| `f_8351d260` | `DepthBuffer_EdgeAdjust` | Allocates `agl::DepthBufferEx::EdgeAdjust` and `BlurAdjust` | read |
| `f_8351dda0` | `HiZ_Make` | Allocates `HiZ Texture`: the depth pyramid | read |
| `f_802ad740` | `OcclusionBuffers_Make` | Allocates `agl::utl::DepthBufferEx::OcclusionDepthBuffer` and `OcclusionColorBuffer` | read |
| `f_80669460` | `ShadowMap_Make` | Allocates `shadow_map_depth`: the sun's cascades | read |
| `f_809ec950` | `StaticShadow_Make` | Allocates `static_shadow_depth` | read |
| `f_802640a0` | `LocalShadow_Make` | Allocates `local_shadow_depth` | read |
| `f_835688b0` | `Vsm_Make` | Allocates `vsm_temp` (a variance shadow map; `esm_blur_tmp` in `f_836236b0`) | read |
| `f_80664830` | `ScreenSpaceShadow_Make` | Allocates `ScreenSpaceShadow` | read |
| `f_8065ddf0` | `ShadowPrePass_MakeTemp` | Allocates `shadow_prepass_light_buffer_temp` and its half-size copy | read |
| `f_8065e180` | `ShadowPrePass_Make` | Allocates `shadow_prepass_light_buffer` | read |
| `f_835a4a70` | `LightPrePass_Make` | Allocates `light_prepass_light_buffer` | read |
| `f_80660010` | `Ssao_MakeWork` | Allocates `ssao_work` and `ssao_work_depth` | read |
| `f_80663c20` | `Ssao_Make` | Allocates `ssao` | read |
| `f_83624120` | `Ssao_MakeMask` | Allocates `ssao_reduced_depth` and `ssao_mask` | read |
| `f_80289590` | `FogPrePass_HalfDepth` | Allocates `FogPrePass::HalfDepth`; also copies textures | read |
| `f_8028b110` | `FogPrePass_Water` | Allocates `FogPrePass AfterWater` and `FogPrePass BeforeWataer` | read |
| `f_80669b80` | `WetMask_Make` | Allocates `wet_mask` | read |
| `f_83519420` | `Sky_MakeScatteringLuts` | Allocates `Single Irradiance`, `Single Scattering Rayleigh`, `Single Scattering Mie` and `delta J`: the sky's precomputed atmospheric scattering tables | read |
| `f_8360b600` | `Reflection_Make` | Allocates `reflection_color`, `reflection_depth`, `reflection buffer`, `reflection_reduce`, `reflection_color_blur` | read |
| `f_8025eb40` | `CubeMap_Make` | Allocates `render_cubemap_depth` and `render_cubemap_color` | read |
| `f_8361c3f0` | `Decal_Make` | Allocates `decal_drawer_buffer` (decal AO: `decalAoTmp`, `f_8361ef90`) | read |
| `f_834c1e80` | `VolumeMask_Make` | Allocates `volume mask - mask` (and its layer, depth and shadow in `f_834c20a0`) | read |
| `f_8026a9e0` | `Bloom_Make` | Allocates `bloom_ex`, `bloom_ex_tgt`, `reduce` and `detect0`/`detect1` | read |
| `f_83515da0` | `Dof_MakeCoc` | Allocates `near_coc_tex`, `far_coc_tex`, `coc_map_tex` and `tmp_tile` | read |
| `f_80280270` | `Dof_MakeMips` | Allocates `dof_blur_mipmap`, `dof_blur_mipmap_weight`, `dof_depth_mipmap` | read |
| `f_8357aa80` | `MotionBlur_Make` | Allocates `aglMotionBlur::tmp_tex` | read |
| `f_8361b770` | `RadialBlur_Make` | Allocates `radial_blur` (`radial_blur_HalfBuffer` in `f_8361b290`) | read |
| `f_835a1ae0` | `MultiFilter_HdrCompose` | Allocates `MultiFilterHDRCompose`, the last of the post chain (`MultiFilterReduce`, `Expand`, `ColorDrift`, `Trimming` in `f_835a0a30`..`f_835a17d0`) | read |
| `f_809ec0e0` | `MultiFilter_ColorCorrection` | Allocates `MultiFilterColorCorrection` | read |
| `f_80271420` | `Ndsr_MakeTargets` | Allocates `ndsr::two_step_upscale_input` and `ndsr::smaa_output` | read |
| `f_80271be0` | `Smaa_MakeTargets` | Allocates `ndsr::smaa_edge`, `smaa_blendweights`, `smaa_edge_list`, `smaa_stencil` | read |
| `f_8360f9a0` | `EdgeAa_MakeTargets` | Allocates `stencilDepth`, `lineMap`, `edgeStorage`, `weightMap`, `temporaryScreen`, `luma`: another edge anti-aliasing | guess |
| `f_8065c660` | `Reprojection_Make` | Allocates `Reprojection` and `DepthBuffer`: a temporal reprojection | guess |
| `f_8065f190` | `ShadingRate_Make` | Allocates `shading_rate_image` | read |
| `f_8028ca00` | `SkinShading_Make` | Allocates `Blur0`, `Blur1`, `Reduce` and `skin_shading` | read |
| `f_836ba520` | `Outline_Make` | Allocates `OutlineDepth` | read |
| `f_8065afe0` | `Snapshot_Make` | Allocates `SnapshotColor`, `SnapshotDepth` and the stroke textures | read |
| `f_809ed030` | `RenderDisplay_Make` | Allocates `agl::RenderDisplay(color)` and `(depth)` | read |
| `f_8006ec60` | `PauseTexture_Make` | Allocates `pause texture`: the paused picture | read |
| `f_83b83360` | `Ndve_Output` | Allocates `NDVE_Output` | guess |

## What the renderer recognises

| What | How the renderer knows it | Why |
|---|---|---|
| The lighting composite (fog and haze over the forward-shaded scene) | program `1a7482c0ec9991ea` | drawn this frame: the world is up (the ultrawide side fill) |
| The item wheel's background | program `02ff9920c2571cc2` | the last frame, blurred |
| The screen-space shadow mask | program `3427a1dbefd5e02c`: a full-screen draw into an RGBA8 mask, MIN-blended | `mask_stabilize` (off since 11 November: a GPU hang near Hyrule Castle) |
| The foliage's noise volumes | program `53509044401d78d2`: 128 draws into two 64³ BC4 volumes as an area loads | trees bare at mid distance when skipped (#1151) |
| The TAAU network | program `fd029a317a9a4b94` | its input, output and the lost blue channel (issue 52) |
| The dynamic-resolution resolve | program `9dc98871eb7fc101` | left out on AMD, the picture was black |
| The final pass | code key `73320e720a317817`; exposure at c3+0x38 | `RX_VK_EXPOSURE_LOG` |
| The meshlet culling | code key `2c56dc1006942f74` (about 30 programs) | the Steam Deck's timeout (issue 817) |
| The tone map | any fragment stage with the ACES-fit constants | HDR output (#2481) |
| The scene's colour, depth and camera | the largest B10G11R11 colour target, its draw's depth and its UBO 3 | DLSS, FSR, frame generation |
| Shadow maps | square depth-only targets: 768 and below the characters', above it the sun's cascades | depth bias (`RX_VK_SHADOW_BIAS`, `RX_VK_CASCADE_BIAS`) |
| Terrain tiles | 260 x 260 RG8, nine levels | the drawn write-back ([textures.md](textures.md)) |

## Fixes by pass

- **Fog and haze:** light probes past the pool's end (white haze, `RX_VK_BARE_READ_MB`); a clear gets a span of its own (a
  flat band of fog); presentation work no longer runs ahead of scene work (last frame's fog and sun shafts, PR 430). The
  OoT fog (0.25) and exposure (0.65) factors that `docs/design.md` still calls necessary were removed
  (HS #2042, LS issue 124).
- **The white forest:** on Intel the EQUAL depth test becomes LEQUAL with a bias (`RX_VK_EQ_BIAS`), and uniforms are
  snapshotted (`RX_VK_UB_SNAPSHOT`, #2419): the prepass drew the ground with other objects' matrices.
- **The sky:** its 256 x 256 LUT is never evicted (the rainbow sky).
- **Water:** a storage pointer read as a texture handle (flicker); AMD's noperspective varyings (the Royal Family's Tomb,
  #1830).
- **Shadows:** biases above; the grass shadow's points topology (AMD stripes, issue 302).
- **Bloom:** unwritten components masked (NaNs on AMD, `RX_VK_ALL_COMPONENTS`); colour outputs NaN-guarded
  (`RX_VK_NAN_GUARD`).
- **Mesh shaders** (foliage, grass, leaves, fireflies, effects): the compute fallback on Navi 21/22 (#996) and by default on
  Linux AMD (#2362), built without the optimizer on AMD (`RX_VK_MESH_OPT`), the fallback's depth hold (sky through grass).
- **Cloth:** built with the optimizer off on AMD Windows (#461, #1540).

## Mapping a frame

To name every one of the renderer's spans in one frame, in a gameplay frame (the present number from a run's log):

- `RX_VK_PASS_TRACE=1` with `RX_VK_PASS_TRACE_AT=<n>`: each span's size, format, draws, clears, targets and programs, in
  order. The best single map of the frame.
- `RX_VK_DUMP=<present>` with `RX_VK_DUMP_W=1024`: a picture of every colour target as its span ends, and of every image a
  dispatch wrote, named `<step>_pass c<i> prog <key> #<tex id>_<address>.png`.
- `RX_VK_PASS_TIMING=2`: GPU time per span and per program.

Matching the spans' sizes and formats, and the pictures, to the targets above names them: done once, for the traced frame
above.

## Open questions

- The spans not yet identified (rows 4, 6, 11 and 14 of the traced frame, and the Deku Tree's 80 x 33 spans).
- A frame from an indoor room and a town (the Market teleport did not land).
- Which of the game's `Create*` switches are on in Ocarina of Time (the values are in the game's data, not the code).
- The two edge anti-aliasings (`ndsr::smaa_*` and `lineMap`/`weightMap`), the `Reprojection` target, the shading-rate image
  and `NDVE_Output`: which of them run.
- A named-target log: a hook at the allocator (`f_8005ed00`) that logs each name, size and address would name the
  renderer's textures directly.
