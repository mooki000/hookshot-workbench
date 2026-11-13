# The world's environment

The world manager (`WorldMgr`, the game's `wm::` code) and what it drives: the time of day, the sun and the moon, the
weather, the wind and the clouds, the environment palettes that set the light, fog and sky for each hour and weather, and
the parameter files they come from. Looked at in November 2026 by static analysis of the binary, to see how the game
decides that the sun is visible and what it changes when the sun is hidden (the open question in
[culling.md](culling.md) under *The sun*), and where the fog and exposure that LS's renderer has struggled with
are set.

**LS code:** `host/hos_main.c` (`oot_set_time`, the debug tools' time of day),
the fog and exposure factors that were in `gpu/vk/vk_shader.cpp` (`oot_visual`) and
`oot_visual_workaround.h` until HS #2042 removed them, `gpu/vk/vk_replay.cpp` (the samples-passed reports, which
the sun's haze followed: PR #487), `gpu/vk/vk_memory.cpp` (the exposure meter's readback page)
**See also:** [events.md](events.md) (`ExecuteWmSetTime` and the world manager's holder), [frame-time.md](frame-time.md)
(`f_803edf70`, the time of day above 60 fps), [rendering.md](rendering.md) (the sky's tables, the froxel fog, the
lighting composite), [culling.md](culling.md) (occlusion queries), [jobs.md](jobs.md), `docs/design.md`

## In short

1. **One world manager, a request block each frame.** Script nodes (`ExecuteWm*`, `HoldWm*`, `OneShotWm*`) don't change
   the world directly: each calls a setter on the world manager that, under its lock, sets a bit in a 64-bit request word
   and writes its values into the request block being filled. Once a frame `WorldMgr_Calc` (`f_803ec480`, run as a job)
   swaps the two blocks, carries the persistent bits over to the new one, clears the one-frame bits, and runs the clock,
   the environment's updates and the sky on the block it took. **read**
2. **The time of day is the clock's.** The clock (`[WorldMgr+0x510]`) holds the seconds of the day and the day; it is
   stepped by `f_803edf70` ([frame-time.md](frame-time.md)) at the frame's step times two rate factors, and stands still
   while one request bit (`0x800000`) or a manager flag is set. `ExecuteWmSetTime` hands it `TimeHour * 3600 + TimeMin *
   60`. **read**
3. **The sun model's visibility is not an occlusion result.** The environment object (`[WorldMgr+0x578]`) keeps the sun
   and the moon's state in one flag word. "The sun is appearing" is a day/night value; "the sun model is visible" is a copy
   of it, cleared while `HoldWmSunMoonInvisible` or `ExecuteWmSetSunMoonModelInvisibleForTimeline` is in force, and both
   are cleared by a flag in the update's arguments. Nothing in that update reads a query count. The lens flare has its own
   enable bit: a time window, unless `ExecuteWmSetDisableLensFlare` is in force. **read**
4. **The sun's occlusion is measured on the GPU.** A compute program named `effect_sun_occlusion_cs` binds the reduced
   depth (`cReducedBuffer`), its parameters and an `Occlusion` buffer; the cloud layers have a `SunOccBufSize`, the
   volumetric clouds a `SunOccChkSize`, and the lens flare's elements `IsEnableOccludedScaling`, `IsEnableOccludedDec` and
   `IsEnableOccludedAlpha`. No CPU reader of that buffer was found. **read** (the names), **guess** (that the GPU reads it)
   So what PR #487 saw (the haze brightening when the counts dropped) most likely went through what the counts cull (see
   [Notes](#notes)), not through a sun query. **guess**
5. **`SkyOcclusion*`, `InCaveOcclusion*` and `InDoor*` are sound parameters**, volumes and filters for the ambient and
   weather sounds, not graphics. **read**
6. **The light, fog and sky come from palettes.** An environment palette (`ResEnvPalette`) sets exposure, the lights,
   the volume fog, white balance, bloom, the sky's scattering and the plain fog; a time table picks palettes by the hour
   and the weather; fixed, area-unique, demo and indoor palettes override parts of it. All are `.gyml` files under
   `Work/WorldMgr/`, packed in `Pack/EnvPalette.pack`. **read**

## The world manager

The world manager is `[0x84e9eab0]` (the holder `0x84c9e898` that [events.md](events.md) documents is the address of that
variable). Its setters all have one shape: take the lock at `+0x490`, pick the request block being filled
(`+0xde8 + index * 0x11a0`, the index at `+0x3128`), set a bit in its first word and store any values, unlock.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_803ec2a0` | `WorldMgr_CalcJob` | A job delegate's invoke (slot 2 of the vtable at `0x84c14638`): takes the frame's arguments and the world manager (`delegate+0x28`) and calls `f_803ec480` | read |
| `f_803ec480` | `WorldMgr_Calc(world, args)` | The world manager's frame. Under the lock it flips the index at `+0x3128`, copies the block it took into the new one and keeps only the persistent bits (the mask `0xffffe07edfd80004`). Then it sets the clock's rate (below), calls `f_803edf70` (the time of day) with the taken block, then `f_803e9130` and `f_803eb170` on the environment object (`+0x578`), and `f_803ed930` when `+0x4d1` bit 2 is set. It copies request bit 20 into `+0x4d0` bit 11 | read |
| `f_803edb40` | `WorldMgr_LoadWorldParam` | Formats `Work/WorldMgr/WorldParam/%s.game__wm__WorldParam.gyml` and makes the environment object it stores at `+0x578` (with `f_81216060`) | read |
| `f_803eed30` | `WorldParam_Params` | Declares the WorldParam's fields: `WorldType`, the base palettes (`BasePalette_Bluesky`, `BasePalette_Cloudy`), the wind's limits, `IsSolarMotion`, `SunRiseAzimuth`, `SunElevation`, `MoonElevation`, `SunriseHoure`/`SunriseMin`, `SunsetHoure`/`SunsetMin`, `IsExistSunMoon`, `SunMoonDist`, `SunSize`, `MoonSize`, `CloudShadowOnOff`, the volume fog's start and end distances, noise and resolution scales (`VolumeFogResScaleConsole`, `VolumeFogResScaleHandheld`), and others | read |
| `f_834722f0` | `WorldMgr_RequestImmTrans` | `ExecuteWmRequestImmTrans`: sets bit 0 of `+0x4d4`, which the frame passes on as the environment update's "immediate" flag | read |
| `f_83472300` | `WorldMgr_SetCloudShape(world, …)` | `ExecuteWmSetCloudShape`: request bit `0x1`, the shape in the block's `+0xc`, `+0x10`, `+0x14` | read |
| `f_834723b0` | `WorldMgr_SetCloudShadowPos(world, x, y)` | `ExecuteWmSetCloudShadowPos`: request bit `0x2`, the position at `+0x18`, `+0x1c` | read |
| `f_83473380` | `WorldMgr_SetWeather(world, name, …)` | `HoldWmSetWeather`: looks the weather up by name, then writes it into the block. `f_83473830` calls it with an empty name (`HoldWmSetWeatherDefault`) | guess |
| `f_83473840` | `WorldMgr_SetEnvEffect` | `HoldWmSetEnvEffect` and `HoldWmSetForceDefaultEnvPalette`: request bit `0x800` and a value at `+0x9e4` | read |
| `f_834738c0` | `WorldMgr_HoldSunMoonInvisible` | `HoldWmSunMoonInvisible`: request bit `0x40000` (one frame, so the hold sets it every frame) | read |
| `f_83473970` | `WorldMgr_SetEnvPalette` | `ExecuteWmSetEnvPaletteForTimeline` and `…ForFlowchart`: the palette names at `+0xa18` and `+0xb38` | read |
| `f_83473b80` | `WorldMgr_ResetEnvPalette` | `ExecuteWmResetEnvPaletteForFlowchart`: request bit `0x200000` | read |
| `f_83474560` | `WorldMgr_SetCloudShadowOff` | `ExecuteWmSetCloudShadowOffForTimeline`: request bit `0x400000`; the sky then uses 0 for the cloud shadow's rate | read |
| `f_83474620` | `WorldMgr_SetWorldShadow` | `ExecuteWmSetWorldShadowForTimeline`: a value at `+0x112c` | read |
| `f_834747e0` | `WorldMgr_SetSunMoonModelInvisible` | `ExecuteWmSetSunMoonModelInvisibleForTimeline` (its slot 29): request bit `0x8000000`, persistent | read |
| `f_83474840` | `WorldMgr_ClearSunMoonModelInvisible` | Clears `0x8000000`; called from the same node's slot 31 (`f_828b0d00`) | read |
| `f_834748a0` | `WorldMgr_SetDisableLensFlare` | `ExecuteWmSetDisableLensFlare`: request bit `0x1000000000`, persistent | read |
| `f_83474900` | `WorldMgr_ResetDisableLensFlare` | `ExecuteWmResetDisableLensFlare`: clears it | read |
| `f_834749d0` | `WorldMgr_SetWorldBaseParam` | `ExecuteWmSetWorldBaseParamForTimeline`: request bit `0x1000000`, a value at `+0x10e0` | read |
| `f_83474f10` | `WorldMgr_SetLunarAge` | `ExecuteWmSetLunarAgeForTimeline`: the lunar age at `+0x10fc` | read |
| `f_83474fb0` | `WorldMgr_SetWeatherTransition` | `ExecuteWmSetWeatherTransitionForTimeline` and `OneShotWmSetWeatherForFlowchart`: request bit `0x10000000`, values at `+0x10d0` and `+0xfb0` | read |
| `f_83475120` | `WorldMgr_ResetWeather` | `OneShotWmResetWeatherForFlowchart`: request bit `0x20000000` | read |
| `f_83475190` | `WorldMgr_SetWind` | `ExecuteWmSetWindForTimeline` and `…ForFlowchart`: request bits `0x40000000` and `0x100000000`, the wind vector at `+0x1100 … +0x1108`. `ExecuteWmResetWindForFlowchart` goes to `f_83475250` | read |
| `f_834752d0` | `WorldMgr_SetUsePlayerPos` | `OneShotWmSetUsePlayerPosForEvent`: request bit `0x100000` | read |
| `f_80c91790` | `WorldMgr_SetSunActor(world, actor)` | Points the actor link at `+0x528` at the sun's actor (or releases it for 0) | read |
| `f_80c91830` | `WorldMgr_SetMoonActor(world, actor)` | The same for the moon, `+0x540` | read |

The world manager's setter for the time, `f_834722d0`, has its row in [events.md](events.md).

## The script nodes

The nodes' own functions (their vtable slots: 29 for an Execute node's run, 39 for a Query or OneShot node's, 40 for a
Hold node's; [master-quest.md](master-quest.md) has the layout). Most nodes are a thin call into a setter above.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_828b0e80` | `ExecuteWmSetTime_Run` | Calls `f_834722d0` with `TimeHour * 3600 + TimeMin * 60` (its inputs at `node+0x20` and `+0x28`) on `[[0x84c9e898]]` | read |
| `f_828b0cf0` | `ExecuteWmSetSunMoonModelInvisibleForTimeline_Run` | Calls `f_834747e0` | read |
| `f_8320b6d0` | `HoldWmSunMoonInvisible_Hold` | While its input is 1, calls `f_834738c0` | read |
| `f_80c916f0` | `OneShotWmSetSunActor_Run` | With `IsSun` set, gives the world manager its actor as the sun (`f_80c91790`), otherwise as the moon (`f_80c91830`); with no actor, releases it | read |
| `f_82a3faf0` | `QueryGetSunDir_Evaluate` | Outputs `SunDir`, `IsSunAppearing` and `IsSunModelVisible` from the environment object's slots `+0x88`, `+0x70` and `+0x78` | read |
| `f_82a373f0` | `QueryGetMoonDir_Evaluate` | The moon's outputs `MoonDir`, `IsMoonAppearing`, `LunarAge` and `IsMoonModelVisible`, from the environment object's slots `+0xa8`, `+0x90`, `+0xb0` and `+0x98` (their getters below) | read |
| `f_82b22d10` | `QueryWorldGetCurrentTime_Evaluate` | Splits the environment's seconds of the day (slot `+0x30`) into its outputs | read |
| `f_83209860` | `HoldWmGetTimeDiv_Hold` | Outputs `Day` (the time division is 0) and `Night` (it is 1), from slot `+0x38` | read |
| `f_82b22b40` | `QueryWeather_Evaluate` | Outputs the weather (slot `+0xf0`), 0 when it is above 4 | read |

## The environment object

`[WorldMgr+0x578]`, made from the WorldParam. Two vtables were seen, `0x84c12040` and `0x84c123a0` (the start of each is
a **guess**), with the same getters; they differ at `+0x220`.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_803eb170` | `WorldEnv_Update(env, args, block)` | Builds the sky's arguments, calls the object's `+0x220` with the block, then `f_803ce120` on `env+0x10` | read |
| `f_803ce120` | `WorldEnv_UpdateSunMoon(sky, args, block)` | The sun, moon, sky and clouds' state from the block (about 4,100 instructions). The flag word at `sky+0x28` (`env+0x38`): its bits as in *Structures*. Among what it reads: request bits `0x40000` and `0x8000000` (the models hidden), `0x1000000000` (no lens flare), `0x400000` (no cloud shadow: the rate at `+0xb70` is 0), `0x2` (the cloud shadow's position), `0x10000000` and `0x20000000` (the weather) | read |
| `f_803e9130` | `WorldEnv_UpdateMain(env, args, block)` | The environment's other update: looks up an entry named `Main`, takes the wind vector from the block when request bit `0x40000000` is set, and reads request bits `0x2000000000` and `0x4000000000` | read |
| `f_83444700` | `WorldEnv_IsSunAppearing` | Slot `+0x70`: bit 4 of `env+0x38` | read |
| `f_83444710` | `WorldEnv_IsSunModelVisible` | Slot `+0x78`: bit 7 of `env+0x38` | read |
| `f_83444730` | `WorldEnv_SunDir` | Slot `+0x88`: the sun's direction, `env+0xb0c` | read |
| `f_83444740` | `WorldEnv_IsMoonAppearing` | Slot `+0x90`: bit 5 of `env+0x38` | read |
| `f_83444750` | `WorldEnv_IsMoonModelVisible` | Slot `+0x98`: bit 8 (bit 0 of `env+0x39`) | read |
| `f_83444770` | `WorldEnv_MoonDir` | Slot `+0xa8`: `env+0xb24` | read |
| `f_83444010` | `WorldEnv_LunarAge` | Slot `+0xb0`: the clock's `+0x68` | read |
| `f_802c8de0` | `WorldEnv_SecondsOfDay` | Slot `+0x30`: the clock's (`env+0xd48`) seconds of the day, at `+0x28`, or at `+0x58` when the clock's `+9` bit 1 is set (a second time: `OneShotRequestVisualOnlyTime` is a likely user, **guess**) | read |
| `f_8006cd50` | `WorldEnv_TimeDivision` | Slot `+0x38`: the clock's `+0x40` (0 day, 1 night) | read |

## The clock

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80f0e640` | `Clock_SetTime(clock, seconds, flag, day)` | What `f_834722d0` calls with `(seconds, 1, -1)`: stores the seconds and the day at `+0x48` and `+0x4c` and sets bits 1 (and 2, when `flag` bit 0 is set) of `+8`, then builds a larger request on the stack | read |

`f_803edf70`, which steps the seconds of the day (`+40`) and the day (`+44`), has its row in [frame-time.md](frame-time.md).
It gets the taken request block and tests request bits `0x1000000` and `0x2000000`. The rate it steps by is set in
`WorldMgr_Calc`: the frame's step times `+0x3768` and times `+0x376c` (kept at `+0xde0`), or 0 while request bit
`0x800000` or `+0x4d0` bit 13 is set; `+0x4d0` bit 14 says the clock is stopped. **read** (who sets `0x800000` was not found)

## Palettes and parameter files

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80fd3370` | `WmResMgr_LoadPacks` | `wm::ResMgr`: loads `Pack/EnvPalette.pack` and `Pack/DemoPalette.pack` and sorts their files by type (`ResEnvPalette`, `ResFixedEnvPaletteParam`, `ResEnvPaletteTimeTable`, `ResEnvPaletteAreaUnique`, `ResWeatherInfluenceParam`) | read |
| `f_81215080` | `ResEnvPalette_Load` | Formats `Work/WorldMgr/ResEnvPalette/%s.game__wm__ResEnvPalette.gyml` | read |
| `f_8119a030` | `ResEnvPaletteTimeTable_Load` | Formats `Work/WorldMgr/ResEnvPaletteTimeTable/%s.game__wm__ResEnvPaletteTimeTable.gyml` | read |
| `f_81197aa0` | `ResEnvPaletteAreaUnique_Load` | Formats `Work/WorldMgr/ResEnvPaletteAreaUnique/%s.game__wm__ResEnvPaletteAreaUnique.gyml` | read |
| `f_8343ba70` | `ResFixedEnvPalette_Load` | Formats `Work/WorldMgr/ResFixedEnvPalette/%s.game__wm__ResFixedEnvPaletteParam.gyml` | read |
| `f_811f6b40` | `ResDemoPalette_Load` | Formats `Work/WorldMgr/ResDemoPalette/%s.game__wm__ResDemoPalette.gyml` (the cutscenes' palettes) | read |
| `f_811971a0` | `ResWeatherInfluenceParam_Load` | Formats `Work/WorldMgr/ResWeatherInfluenceParam/%s.game__wm__ResWeatherInfluenceParam.gyml` | read |
| `f_82849ab0` | `VolumeFogAreaParam_Load` | Formats `Work/System/GameGraphics/VolumeFogAreaParam/%s.game__gfx__VolumeFogAreaParam.gyml` (its `FogParams`); `ExecuteRegisterVolumeFogArea` registers such areas | read |
| `f_83452160` | `ResEnvPalette_Params` | Declares an environment palette's fields: `ExposureValue` and its corrections, the lights' intensities (character, sub-character, field-object, specular), the bake and sky light scales, the hemisphere light, the volume fog block (`VolumeFogIsEnabled`, `VolumeFogMainLightScale`, the probe light scales, `VolumeFogEnableFog1`/`2`), white balance, bloom, caustics, the sky's sun colour and scattering (`SkyRParam_rayleigh_amplifier`, `SkyRParam_mie_*`), the scatter fog (`SfParam_*`), `CloudShadowRate`, `RainExposureRate`, `HumidityWetRatio`, the plain fog (`FogColor`, `FogStart`, `FogEnd`, `AfParam_attenuationForGrd`/`ForSky`), `AmbientIntensity` and the cube map's ambient and fog | read |
| `f_83451e40` | `ResEnvPalette_VolumeFogParams` | The palette's two volume fog blocks, `VolumeFogParams1` and `VolumeFogParams2` (`game__wm__ResEnvPalette_VolumeFog`) | read |
| `f_83466620` | `FixedEnvPalette_Params` | Fields that each come with an `IsOverwrite…` switch: exposure, character light, specular, hemisphere light, the volume fog's scales and densities (`VF…`), white balance, bloom, caustics, wetness | guess |
| `f_834477e0` | `DemoPalette_Params` | A cutscene palette's fields: a fixed sun direction (`IsDirectSunAzimuth`, `DirectSunElevation`, …), fog and volume fog overrides, exposure compensation, character lights, local depth shadow, vignetting, Navi's light, bloom | guess |
| `f_831d2870` | `VolumeFogCommonParam_Params` | `NoiseParams` (`game__gfx__VolumeFogNoiseParam`) and `Fog1Params`/`Fog2Params` (`game__gfx__VolumeFogCommonParam`) | read |

`Work/WorldMgr/WorldParam/MainField.game__wm__WorldParam.gyml` and `Viewer.game__wm__WorldParam.gyml` are named outright,
as are `ResEnvPalette/Default` and `ResEnvPaletteTimeTable/MainField_Bluesky`; `PrequelPrCloud/%03d` and
`PrequelDofMgrParam/000` are the other `Work/WorldMgr/` files.

## The sky, the sun's occlusion and the lens flare

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80b6bdd0` | `SkyScattering_Bind` | Uses the atmosphere tables' names: `cTexTransmittance`, `cTexDeltaSR`, `cTexDeltaSM`, `cTexIrradiance`, `cTexDeltaE`, `cTexDeltaJ`, `cTexInscatter`, `cTexBakedInscatter`, `cTexBakedIrradiance`, `cTexFogDensity`, `cTexCloud`: the sky's precomputed scattering ([rendering.md](rendering.md): `Single Scattering Rayleigh`/`Mie`) | read |
| `f_80b673a0` | `SceneAnalyze_Bind` | Uses `ShadowAnalyzeParam`/`Result`, `SkyAnalyzeParam`, `ProbeAnalyzeParam`/`Result`, `InnerMaskAnalyzeParam`/`Result`, `SceneShadingAnalyzeInfo`, `cTexBakedInscatter` and `cQuatDepth`: a GPU analysis of the scene from the quarter-size depth | read |
| `f_80ddaa40` | `SunOcclusion_Construct` | Makes a 0x40-byte object (vtable `0x84baa188`) that `f_80ddab00` keeps at `[gfx+0xb50]` | read |
| `f_80ecdd80` | `SunOcclusion_Init` | Finds the program `effect_sun_occlusion_cs` (the name at `0x84baa198`) and makes two small buffers in each 0x118-byte entry | read |
| `f_80b693b0` | `SunOcclusion_BindProgram` | Finds `effect_sun_occlusion_cs` and binds `cReducedBuffer` (a texture), `cParameters` and `Occlusion` (a buffer) | read |
| `f_834d13c0` | `CloudLayerParam_Params` | A cloud layer's fields: `IsEnable`, `IsDrawReduceBuffer`, `IsDisableFarClip`, `IsDisableDepthTest`, `IsSyncSunPosition`, `DrawOrder`, `CloudColorScale`, `FogColor`/`Near`/`Far`, `AttenuationForSky`, `SunOccBufSize` (next to the name `agl__CloudLayerParam`) | guess |
| `f_834cb420` | `VolumetricCloudParam_Params` | The volumetric clouds' fields, among them `ScatterHeight`, `ScatterAmb`, `SunOccChkSize` and the noise and backlight parameters | guess |
| `f_834f7570` | `LensFlareElement_Params` | A lens flare element's fields: textures, position, rotation, size, colour, and `IsEnableOccludedScaling`, `IsEnableOccludedDec`, `IsEnableOccludedAlpha`, `IsEnableAngleOcclusion` | read |

The pipeline has a switch for the occluded effect (`CreateOccludedEffect`, [rendering.md](rendering.md)) and limits for
lens flares (`OfxNumLensFlare` and its rigs, presets and dynamic presets). The agl shader table lists
`occluded_effect_lensflare` beside `occlusion_query`, `occlusion_renderer` and `occlusion_renderer_clear_buf`. **read**
(the names)

## The sound's occlusion

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83357770` | `EnvSoundOcclusion_Params` | Declares `SkyOcclusionNear/Far/SHeavySnow` `VolumeMin`/`FilterMax`, the same for `InDoor`, `InDoorDist` and `Height`: how far a sound's volume and filter drop under a roof, indoors and in heavy snow | read |
| `f_81097c50` | `WindSound_Params` | The wind sound's fields, among them `InCaveOcclusionVolumeBasic`/`Strong` and `InCaveOcclusionFilterBasic`/`Strong`, `BasicWindCaveRateMin` and `BasicWindSkyRateMin` | read |

## The bake workers

Two worker threads of the baked-lighting side (probes and baked resources). Looked at for the port's virtual-core
waits while a save loads, where both show up as waiters.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80b15710` | `BakeProbe_Init` | Creates the `BakeProbe` worker with its configured priority and a one-core mask, bound to `f_80866480` | read |
| `f_80866480` | `BakeProbe_RunEntries` | The `BakeProbe` worker's body: calls each registered entry while holding the manager's mutex | read |
| `f_80b15d80` | `BakeModuleMgr_Init` | Creates the `bake::ModuleMgr` worker when configured, bound to `f_80b41070`; its setup registers the baked-resource and light handlers | read |
| `f_80b41070` | `BakeModuleMgr_DrainQueue` | The `bake::ModuleMgr` worker's body: pops queued work under a mutex, runs each item with the mutex released, and yields between items | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84e9eab0` | `g_WorldMgr` | The world manager's pointer (`[0x84c9e898]` is this address) | read |
| `0x84baa198` | `SunOcclusion_ProgramName` | A pointer to the name `effect_sun_occlusion_cs`, the key its program is found by | read |
| `0x83f21bb0` | `WeatherNames` | The names `Invalid`, `Bluesky`, `Rain`, `Snow`, `Thunderstorm`, `Cloudy`, in that order (the weather's names; their values were not checked) | guess |

## Structures

### The world manager

| Offset | Field | Sure |
|---|---|---|
| `+0x490` | The requests' lock | read |
| `+0x4b0` | A lock the frame takes first (it does nothing that frame when it can't) | guess |
| `+0x4d0` | Flags: bit 11 a copy of request bit 20 (use the player's position), bit 13 stops the clock, bit 14 the clock is stopped | read |
| `+0x4d4` | Flags: bit 0 "immediate" (`ExecuteWmRequestImmTrans`) | read |
| `+0x510` | The clock (must be set before `f_834722d0` is called) | read |
| `+0x528` | The sun's actor link | read |
| `+0x540` | The moon's actor link | read |
| `+0x578` | The environment object | read |
| `+0xddc` | The frame's step times `+0x3768` | read |
| `+0xde0` | The clock's rate: `+0xddc` times `+0x376c`, or 0 while stopped | read |
| `+0xde8` | Two request blocks of 0x11a0 bytes | read |
| `+0x3128` | The index of the block being filled (0 or 1) | read |

### A request block (0x11a0 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0` | The request bits (64-bit). Kept from frame to frame: those in `0xffffe07edfd80004`, among them `0x100000`, `0x400000`, `0x1000000`, `0x8000000`, `0x10000000`, `0x40000000`, `0x1000000000`. One frame only: the rest, among them `0x1`, `0x2`, `0x800`, `0x40000`, `0x200000`, `0x20000000`, `0x100000000` | read |
| `+0xc … +0x14` | The cloud shape (bit `0x1`) | read |
| `+0x18`, `+0x1c` | The cloud shadow's position (bit `0x2`) | read |
| `+0x9e4` | The environment effect (bit `0x800`) | read |
| `+0xa18`, `+0xb38` | Palette names | read |
| `+0x10fc` | The lunar age | read |
| `+0x1100 … +0x1108` | The wind vector | read |

### The clock

| Offset | Field | Sure |
|---|---|---|
| `+8` | Flags: bits 1 and 2 set by `Clock_SetTime`; `0x30`, `0xc0` and `0x200` tested by the frame | read |
| `+9` | Bit 1: read the second time block (`+0x50`) instead of the first (`+0x20`) | read |
| `+40` | The seconds of the day (0 to 86399) | read |
| `+44` | The day | read |
| `+0x40` | The time division (0 day, 1 night) | read |
| `+0x48`, `+0x4c` | The time and day asked for by `Clock_SetTime` | read |
| `+0x68` | The lunar age | read |

### The environment object's sun and moon flags (`env+0x38`, 32-bit)

| Offset | Field | Sure |
|---|---|---|
| bit 2, bit 3 | Day, night (the day/night value passed in the update's arguments is 0 or 1); bit 3 is slot `+0x68` | read |
| bit 4 | The sun is appearing (day), cleared with bit 5 by a flag in the arguments (`args+5`) or `+0xd08` bit 2 | read |
| bit 5 | The moon is appearing (night) | read |
| bit 7 | The sun model is visible: a copy of bit 4, cleared while request bit `0x40000` or `0x8000000` is set | read |
| bit 8 | The moon model is visible: a copy of bit 5, cleared the same way | read |
| bits 9, 10 | Bits 7 and 8 of the frame before | read |
| bit 11 | The lens flare is on: set when the time of day is inside a window worked out modulo 86,400 seconds, and request bit `0x1000000000` is clear | read |

## Notes

### How the sun's visibility is decided

The sun model's visibility (`IsSunModelVisible`, bit 7 above) depends on three things only: whether it is day, whether
the world shows the sun and moon at all, and whether a script hides the models. **read** Whether terrain, a wall or a cave
ceiling is in front of the sun doesn't enter into it.

What the window for the lens flare's bit is was not worked out; the WorldParam's `SunriseHoure`/`SunriseMin` and
`SunsetHoure`/`SunsetMin` are the likely bounds. **guess**

The sun's actor (`OneShotWmSetSunActor`) and the queries `QueryGetSunDir` and `HoldCheckIsReticleDirectedToTheSun` are how
the game's scripts see the sun. The CPU-read sphere queries (`QueryGenericIsVisibleByOcclusionQuery`,
[culling.md](culling.md)) are read only by that script node; which actors' scripts use it is in their AI data, not the
code. Whether the sun's actor is one of them was not checked. **open**

### What PR #487 saw

With the per-draw count, a samples-passed report sometimes read 0, and for that frame "the sunlight left the ground and the
haze brightened over the whole picture" (PR #487). The world manager and the environment object read no query, so the
sun's own state did not change. Two paths fit what was seen, and neither has been traced:

- The z-prepass culling: an object whose count read 0 is left out of the later passes for a frame
  ([culling.md](culling.md)). If the depth that `effect_sun_occlusion_cs` reads (`cReducedBuffer`) or the scene analysis
  reads (`cQuatDepth`) lost the objects in front of the sun, the sun would count as unoccluded and the glare and haze
  would brighten. **guess**
- The scene analysis (`f_80b673a0`): an inner-mask (indoor) and shadow analysis over the quarter-size depth, whose
  results could move the sunlight. **guess**

So the cost that culling.md fears (whatever the game does when the sun is hidden never happens) is a cost to the GPU's
sun occlusion and the culling, if any; the sun model's visibility is not involved. With every report at 4096 nothing is
culled, so the depth is complete and the GPU's own sun occlusion should be right. **guess**

### LS

- `oot_set_time` calls `f_834722d0` directly with `hour * 3600`, as `ExecuteWmSetTime` would with `TimeMin` 0. It checks
  the clock (`world+0x510`) first. **read**
- The time of day above 60 fps is `f_803edf70`'s cut to whole seconds ([frame-time.md](frame-time.md), PR #2093).
- The fog and exposure factors: `docs/design.md` says the OoT fog (0.25) and exposure (0.65) factors are
  still needed, but `oot_visual` in `gpu/vk/vk_shader.cpp` is now off unless `RX_VK_OOT_VISUAL=1`, and its defaults are
  0.08 (`RX_VK_OOT_FOG`) and 1.0 (`RX_VK_OOT_EXPOSURE`). The fog it scales is the froxel fog's integration (a compute
  program; the volume fog of the palettes above), and the exposure is the final pass's (`c3+0x38`). The game sets its
  exposure from the palette (`ExposureValue`, `IsExposureLow`, `RainExposureRate`) and from its auto exposure
  (`agl::pfx::AutoExposure`), whose meter the CPU reads back (`gpu/vk/vk_memory.cpp`).
- The volume fog's resolution is set separately for the console and handheld (`VolumeFogResScaleConsole`,
  `VolumeFogResScaleHandheld`, in the WorldParam).
- The volume fog reads the light probes (`VolumeFogProbeBakeLightScale`, `…SkyLightScale`, `…BounceLightScale`): the
  probes past the pool's end were the white haze ([rendering.md](rendering.md), *Fixes by pass*).

### The environment objects (`EnvObj`)

The lights placed in a map are gsys environment objects: `gsys__EnvObjSetParam`, `gsys__EnvObjSetMgrParam`,
`EnvObjPointLightNum`, `EnvObjSpotLightRigNum`, `EnvObjPlaneLightRigNum`, `EnvObjForLightprepass`. An environment set
(`gsys__EnvSetParam`, applied by `SeqExecuteApplyEnvSet`) has sections for `AreaEnv`, `Bloom`, `DoF`, `ColorCorrect`,
`LightMap`, `Shadow`, `Light`, `AutoExposure`, `OccludedEffect`, `Sky`, `LocalExposure`, `Star`, `Cloud`,
`VolumetricCloud`, `EnvMapRuntime`, `SnapshotEnvObj`, `LightProbe` and others (the names `f_8357e250` uses). Not read
further. **read** (the names)

## Open questions

- What reads the `Occlusion` buffer of `effect_sun_occlusion_cs`, and what does it change: the lens flare's alpha and
  scale (`IsEnableOccludedAlpha`), the clouds' `SunOccBufSize`, the haze? Where is the program dispatched?
- Does the depth that the sun occlusion and the scene analysis read lose the objects the z-prepass culling leaves out?
  That would explain PR #487's flicker.
- The lens flare's time window: sunrise to sunset, or something else?
- Who sets request bits `0x800000` (the clock stopped), `0x2000000` and `0x4000000000`, and what bits 24 and 25 do in the
  time of day.
- Which of the weather's names goes with which value of `QueryWeather`.
- Whether the sun's actor uses `QueryGenericIsVisibleByOcclusionQuery`, and whether its glare depends on it.
- Which volume fog resolution (console or handheld) LS's game runs with.
