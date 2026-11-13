# Audio

The game's sound system: the sound engine on top (alto and aal, with the SLink2 sound links), the audio framework under
it that builds the `nn::audio` renderer's graph and renders on its own thread, the effects it asks for, how the voice
files are found, and how the voice language is picked. It was looked at in November 2026 by static analysis of the binary,
the RomFS file names and LS's logs, for "Voices in the wrong language" (#1460) and for the `nn::audio` imports the
log shows as unimplemented.

**LS code:** `host/nn_audio.c` (the renderer, voices, mixes, splitters, I3DL2 reverb,
voice biquads, the light limiter, the output), `host/nn_audio_in.c` (the input streams: the
ocarina's microphone), `host/nn_applet.c` (`GetDesiredLanguage`: `language.txt`),
`nx2/standalone.py` (which imports get the generic stub),
`third_party/a64-kit/rt/a64_rt.c` (`a64_unimplemented`); tests
`tests/test_audio_*.py`
**See also:** [jobs.md](jobs.md) (`f_80b1ff40`, the SoundModule job), [master-quest.md](master-quest.md) (the region and
language manager, `RegionLangMask.txt`), [game-data.md](game-data.md) (`option.sav`, the game data by hash),
[frame-time.md](frame-time.md)

## In short

1. **Three layers.** At the top, the game's sound code (`game::sound::*` parameters, `OneShotSound*`, `ExecuteSound*` and
   `QuerySound*` script nodes, SLink2 `.bslnk` links). Under it the "alto" system (`AltoConfig`, groups, attenuation,
   the `.bars` list) and "aal" (`aal::Emitter`, its worker threads). At the bottom an audio framework that owns the
   `nn::audio` renderer, its mixes, voices and effects, and renders on "AudioFrameworkThread". **read**
2. **The renderer.** 48 kHz, 240-sample frames, 30 mix buffers, 96 voices, 4 sub mixes, 10 effects, 96 splitters, out
   through one device sink, "MainAudioOut", with the channel map 0 to 5. The thread waits for the renderer's event, then
   updates the graph and calls `RequestUpdateAudioRenderer`. A second mode (manual rendering into a circular-buffer sink,
   written out through an `OutputStream`) is in the code; LS's logs never reach it. **read**
3. **Effects.** In the logs the game adds a **Reverb** (not the I3DL2 one), a **Delay**, a **BiquadFilter** and an
   **Aux** to its sub mixes, and sets splitter biquad filters. LS implements none of these. The stubs log once and
   return 0, which the game takes as success, so the sound goes out dry. LS does implement the I3DL2 reverb, the
   voices' own biquads and the light limiter. **read** (the code), the log (which ones are called)
4. **Voice files** are named by a region-language code: `Voice/Resource/<code>_EventFlowMsg-…bars` and
   `Voice/Resource/Stream/<code>_EventFlowMsg-…bwav`. The dump has nine codes: `JPja`, `USen`, `USes`, `USfr`, `USpt`,
   `EUes`, `EUfr`, `EUde`, `EUit`. `VoiceSettingInfo`'s `LanguageMapping` sends `EUen` to `USen`, and
   `AltLanguageMapping` sends `USfr` to `EUfr` for the lines `USfr` lacks. **read** (the code), the files
5. **The voice language is an option, not the system language.** It is the game-data enum `OptionData.VoiceLanguage`
   (saved in `option.sav`; its default in `GameDataList` is **Japanese**). On its first update the voice manager sets
   it from the system language (`GetDesiredLanguage`, through the region and language). After that, every write of the
   option re-picks the voice files. A value it does not know gives `JPja`. **read**
6. **#1460 (pt-BR, Japanese voices after Gohma):** `pt-BR` gives `USpt` at the start, as the report says. Japanese voices
   later, with the text still in Portuguese, are what the code gives when something later writes
   `OptionData.VoiceLanguage` with Japanese (the game data's default) or with a value outside the nine. **guess** (what
   writes it is not found)

## How LS covers the imports

The game imports 253 `nn::audio` functions. 125 have an HLE in `host/` (`nn_audio.c`, `nn_audio_in.c`). The 4
`nn::audio::detail::Fft*` functions are not OS-facing (`os_facing` in `nx2/standalone.py`), so the SDK's own code runs.
The other 124 get the generic stub: `a64_unimplemented` logs the import once and returns 0 in `x0`, and leaves `s0`
alone (so a float getter returns whatever `s0` held). **read** Every one of the 253 has a direct caller in `main`.

| Feature | What the game imports | LS | What the game gets |
|---|---|---|---|
| Renderer, config, memory pools | Open/Start/Stop/Close, `RequestUpdateAudioRenderer`, elapsed frames, buffer sizes, pools | HLE: a mixer thread renders 240 samples every 5 ms and signals the event | Works. `ExecuteAudioRendererRendering` (manual mode) is stubbed and never called in the logs |
| Voices | Acquire/Release, play state, volume, pitch, mix volumes, destinations, wave buffers, played count, priority | HLE: PCM16, float, DSP-ADPCM, resampled; `SetVoicePriority` ignored; `IsVoiceDroppedFlagOn` always 0 | Works. `SetVoiceSrcQuality` is stubbed (the resampler is LS's own) |
| Voice biquads | `SetVoiceBiquadFilterParameter` (the float form) | HLE (`test_audio_voice_biquad.py`); v2.0.14 logged it as unimplemented, `main` has it | Works on `main` |
| Mixes and splitters | Final and sub mixes, volumes, destinations, splitter destinations and volumes | HLE | Works |
| Splitter biquads | `SetSplitterBiquadFilterParameter` (sub mix and final mix forms), 7 callers each | Stubbed (in the log) | No filtering on splitter sends |
| Reverb (the plain one) | `AddReverb`, its 13 setters, input/output | Stubbed (all in the log) | Dry |
| I3DL2 reverb | `AddI3dl2Reverb`, 26 getters and setters | HLE: a small feedback-delay network (`HOS_AUDIO_NO_REVERB=1` turns it off) | Works; whether the game adds one was not seen in a log |
| Delay | `AddDelay`, 9 setters, input/output | Stubbed (in the log) | Dry |
| BiquadFilter effect | `AddBiquadFilter`, parameter, enabled, input/output | Stubbed (in the log) | Dry |
| Aux | `AddAux`, read/write buffers, sample count | `AddAux` HLE as a no-op; `SetAuxEnabled` and `SetAuxInputOutput` stubbed | The aux reads nothing; the game's aux processing (`f_801e9c80`, `f_806ab700`) gets no samples |
| Light limiter | Add, 10 getters and setters | The setters are HLE (a peak limiter on the final mix); `AddLightLimiter` is stubbed | Not seen added in the logs |
| Compressor, BufferMixer, Capture, CircularBufferSink | Add/Remove, setters | Stubbed | Not seen in the logs |
| OutputStream | Open/Start/Write/Stop/Close, counts, statistics | Stubbed | Only the manual mode and the movie player's second path use it; neither is seen in the logs |
| Device | Channel count, device name, list, `SetAudioDeviceOutputVolume` | HLE | Works |
| Device (other) | Output-device notification, auto-tune volume mode, `GetAudioDeviceOutputVolume`, virtual output devices | Stubbed | `GetAudioDeviceOutputVolume` returns an undefined float (`f_80f72ea0`) |
| Voice drop | `Get`/`SetAudioRendererVoiceDropParameter` | Stubbed | The getter's float is undefined (`f_810442d0` keeps it at `+0x160`) |
| Input streams | Open/Start/Read/…, virtual input devices | HLE (`nn_audio_in.c`, the microphone through SDL) | Works |

The unimplemented `nn::audio` imports in the local logs (v2.0.14, five runs) are 43 in all: `AddReverb`,
`AddDelay`, `AddBiquadFilter` (all on a sub mix), the Reverb and Delay setters, `SetBiquadFilterParameter`,
`SetBiquadFilterEnabled`, `SetBiquadFilterInputOutput`, `SetAuxEnabled`, `SetAuxInputOutput`, both
`SetSplitterBiquadFilterParameter`, `SetVoiceBiquadFilterParameter` (float), `ListVirtualInputDevices` (both have HLEs on
`main`), the voice-drop pair and the four device calls. The `lr` of each matches a function below.

## Functions

### The sound engine at boot

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8104c360` | `AltoSystem_Boot(sys, args)` | Boots "altoSystem": loads `Sound/altoConfig`, `Sound/Group/GroupSetting.%s.byml` (or `.%s.bagst`), `Sound/Attenuation/SPLAttenuation.%s.baatarc`, `Sound/ResourceListPack/BarslistPack.%s.byml` and `SLink2/slink2.%s.bslnk`, each with a plain-name fallback, and sets the stream root `content://Sound/Resource/Stream/`. The `%s` is the string at `[[args+0x28]]+0x148`; the dump's files say it is `Product.111`. "SLinkUserCreate" and "Boot" are also named. Called from `f_83bde660`; calls `f_81049f70` | read |
| `f_83bde660` | `AltoConfig_Load` | Builds `Sound/%s.alto__AltoConfig.bgyml` with "Product" or "All" (the same choice as `f_83be64a0`, [master-quest.md](master-quest.md)) and reads its `ShapeListFileName`; calls `f_8104c360` | guess |
| `f_81049f70` | `AalSystem_Initialize` | Sets up aal: "aal::Emitter", "aal::LowPrioWorkerThread" (started with a core mask), the snd heap, ducking ("ManualDucking", "Pause"), "BiquadFilter" and "OcclusionParam". Calls `f_80fb1730` (the framework's parameters) then `f_81043860` (the framework) | read |
| `f_8104e250` | `Bgm_LoadTable` | Loads `Sound/Bgm/bgm.%s.byml` (falling back to `Sound/Bgm/bgm.byml`), with the same `%s` as the boot | guess |
| `f_80fb1730` | `AudioFrameworkParam_Create` | Allocates the framework's parameter block and fills it with `f_80fb17b0` | read |
| `f_80fb17b0` | `AudioFrameworkParam_Init(p)` | `InitializeAudioRendererParameter`, then overrides the renderer parameter at `p+12`: 48000 Hz, 240 samples a frame, 30 mix buffers, 4 sub mixes, 96 voices, 2 sinks, 10 effects, 96 splitters, 384 splitter sends | read |

### The audio framework and its thread

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_81043860` | `AudioFramework_Initialize(fw, param)` | Runs once (`fw+0x44`). Copies the parameters (the byte `param+0x60` to `fw+4`: the manual mode), opens the renderer (`f_810442d0`), makes the final mix, sub mixes and memory pools, adds the sink (`f_81044c30`) and starts "AudioFrameworkThread" (thread vtable `0x84c38238`, unless `param+0x99` is set). With `fw+4` = 1 it instead opens an `OutputStream` on the default virtual output device, primes it with four blocks and starts it | read |
| `f_810442d0` | `AudioFramework_OpenRenderer(fw, param)` | Allocates the renderer's and the config's work buffers (sizes from the two `Get…WorkBufferSize` calls), `OpenAudioRenderer` (handle at `+0xa8`, event at `+0x130`), `InitializeAudioRendererConfig` (`+0xb8`), `StartAudioRenderer`, and keeps `GetAudioRendererVoiceDropParameter`'s float at `+0x160` | read |
| `f_81044c30` | `AudioFramework_AddMainOut(fw, config, mix, p)` | Unless `p+0` is set, `AddDeviceSink` named "MainAudioOut" with `[p+4]` channels from the map at `0x83f83998` (0 to 5). Then `f_81044f90` | read |
| `f_81044f90` | `AudioFramework_AddCircularSink` | Only when its sink's `+0x54` is 1: `AddCircularBufferSink` with its own memory pool. Not reached in the logs (it would log as unimplemented) | read |
| `f_836f51d0` | `AudioFrameworkThread_Run(thread)` | Slot 14 of `0x84c38238`. While the framework (`thread+0xd8`) runs: in manual mode it calls the frame back to back. Otherwise it waits for the renderer's system event (`f_836f6ff0`, `WaitSystemEvent`), moves itself to the core that `f_83499e60` picks from the table at `thread+0xe0` when that changes (`SetThreadCoreMask`, the core kept at `+0xe8`; 0x21 means none), and calls `f_836f52d0` | read |
| `f_836f52d0` | `AudioFramework_Frame(fw, arg)` | One audio frame: `GetAudioRendererElapsedFrameCount`, the voice and mix updates (`f_836f7040`, `f_83700f40`, `f_836f71f0`), then `RequestUpdateAudioRenderer`. Only when both `arg+4` and `fw+4` are 1 does it call `ExecuteAudioRendererRendering`. Then the wave buffers (`f_801ea5a0`) and the circular sink's read | read |
| `f_836f5c40` | `AudioFramework_CloseOutputStream` | Writes its last blocks, stops and closes the manual mode's `OutputStream` | guess |
| `f_80f72ea0` | `AudioDevice_GetOutputVolume(dev, i)` | `GetAudioDeviceOutputVolume` of output `i` (0 to 2; the names at `[dev+0x188] + 256*i`, likely "AudioTvOutput", "AudioExternalOutput", "AudioBuiltInSpeakerOutput"), 0.0 for others. Stubbed in LS, so `s0` comes back unchanged | read |

### Effects

Each effect has a small class whose methods call the import directly: an "add" (with a sub mix and a final-mix form),
an "apply parameters", an input/output setter, and thin `Remove`/`Is…Enabled`/`Is…Removable` thunks (`0x836fdb10` to
`0x836fe8b0`). The enable call goes through a virtual call in `f_80de9380` (the log's `lr` 0x80de9464 for
`SetReverbEnabled`, `SetDelayEnabled`, `SetAuxEnabled` and `SetBiquadFilterEnabled`).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80de9680` | `ReverbEffect_Add` | `AddReverb` on a sub mix or the final mix (log `lr` 0x80de96ec: the sub mix) | read |
| `f_80de9120` | `ReverbEffect_Apply` | `IsReverbEnabled`, then the early mode and gain, pre-delay, late mode and gain, decay time, HF decay ratio, coloration, reverb, out and dry gains, and the input/output | read |
| `f_80de95c0` | `ReverbEffect_SetInputOutput` | `SetReverbInputOutput` | read |
| `f_80de9990` | `I3dl2ReverbEffect_Add` | `AddI3dl2Reverb` on a sub mix or the final mix | read |
| `f_8013d020` | `I3dl2ReverbEffect_Apply` | Reads and writes all 13 I3DL2 parameters and the input/output (each pair of `Get`/`Set` imports) | read |
| `f_80dd5340` | `DelayEffect_Add` | `AddDelay` on a sub mix or the final mix (log `lr` 0x80dd53b8) | read |
| `f_80dd5280` | `DelayEffect_SetInputOutput` | `SetDelayInputOutput` | read |
| `f_8119f0d0` | `DelayEffect_Apply` | `IsDelayEnabled`, `SetDelayEnabled`, delay time, dry, feedback and in gains, channel spread, low-pass amount | read |
| `f_80dc7430` | `BiquadFilterEffect_Add` | `AddBiquadFilter` on a sub mix or the final mix (log `lr` 0x80dc7494) | read |
| `f_80dc7370` | `BiquadFilterEffect_SetInputOutput` | `SetBiquadFilterInputOutput` | read |
| `f_836fcd20` | `BiquadFilterEffect_SetParameter` | `SetBiquadFilterParameter` (log `lr` 0x836fcdb4) | read |
| `f_80de9800` | `AuxEffect_Add` | `AddAux` on a sub mix or the final mix; `f_80de9750` sets its input/output | read |
| `f_801e9c80` | `AuxEffect_Process` | `GetAuxSampleCount`, `ReadAuxSendBuffer`, `WriteAuxReturnBuffer`, the enable pair: the CPU side of an aux effect (`f_806ab700` is a second one) | read |
| `f_836fd320` | `LightLimiterEffect_Add` | `AddLightLimiter` and `SetLightLimiterProcessingMode`; `f_836fd0d0` applies its parameters | read |
| `f_836fd9a0` | `CompressorEffect_Add` | `AddCompressor`; `f_836fd780` applies its parameters | read |
| `f_80526550` | `Splitter_SetDestinations` | Two `SetSplitterDestination` and four `SetSplitterBiquadFilterParameter` (log `lr` 0x805265f0, 0x805266cc); `f_836f8020` is the same in the framework | read |

### Voices

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80942280` | `AudioVoice_Acquire` | `AcquireVoiceSlot`, then `SetVoiceSrcQuality`; releases a slot that is no longer valid | read |
| `f_801ea760` | `AudioVoice_UpdateWaveBuffers` | The streaming loop: `GetReleasedWaveBuffer`, `GetWaveBufferCount`, `AppendWaveBuffer` (up to four), `GetVoicePlayedSampleCount`, `IsVoiceDroppedFlagOn` | read |
| `f_80f680c0` | `AudioIn_Open` | Opens the input streams (`ListVirtualInputDevices`, `GetTrueRawAudioInputDevice`, two `OpenInputStream`): the microphone that `nn_audio_in.c` serves | read |
| `f_8338d4d0` | `AudioIn_Update` | Reads the input streams and checks an output stream's counts; with the FFT in `f_8338c160` it is likely the ocarina's pitch detection (the CRI "voice tuner": `SeqOneShotRequestReopenVoiceTuner`) | guess |
| `f_83b809d0` | `MovieAudio_Create` | The movie player's sound: starts "MovieAudioRendererThread" | guess |
| `f_83b816f0` | `MovieAudio_OpenOutputStream` | `OpenOutputStream` (the 8-argument form) on the default virtual output device; `f_83b81890` writes and closes it. Not reached in the logs | read |

### The region and the language

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_811f32f0` | `SystemLanguage_GetRegion` | `GetDesiredLanguage()` to a region: ja 0; en-US, fr-CA, es-419, pt-BR 1; en-GB, fr, de, it, es, nl, pt, ru, pl 2; ko 3; zh-Hans 4; zh-Hant 5; th 6; anything else 0. About 25 callers, nearly always with `f_811f35f0` | read |
| `f_811f35f0` | `SystemLanguage_GetLanguage` | `GetDesiredLanguage()` to a language: ja 0, en 1, es 2, fr 3, de 4, it 5, pt 6, nl 7, ru 8, ko 9, zh 10, pl 11, th 12 (the regional forms, en-GB, fr-CA, es-419, pt-BR, zh-Hant, count as their language); anything else 0 | read |
| `f_811f3540` | `LanguageCode_Equals(a, b)` | Compares two 8-byte language codes | guess |
| `f_8349b3f0` | `RegionLanguage_Pick(out, region, language, list)` | The type is `0x83f3a464[language*7 + region]`. If it is in the allowed list (a ring of types), that; otherwise the first type in the list whose region (`0x83f3a440`) is `region`; otherwise the list's first. `f_80fb1600` ([master-quest.md](master-quest.md)) calls it with the list from `RegionLangMask.txt` and stores the type at the manager's `+0` | read |
| `f_809d8f00` | `RegionLanguage_GetType` | The region-language type (0 to 17, codes at `0x83f0f980`): `[0x84e9f558]+0` once the manager is loaded (`+0x40` = 1), otherwise the table `0x83f3a464`, and where that has no entry the region's own default (`0x83f3a4c0`). About ten callers: fonts (`f_809d8d50`), voices, layouts | read |

### The voice language

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_809d48c0` | `VoiceLanguage_SetDefault` | The default voice for the region-language type, from `0x83f2fd28` (type 0, or out of range: Japanese), written to `OptionData.VoiceLanguage` (struct 0x834d9d9e, field 0x74b1edd9) through `f_809d3eb0` / `f_809d3ba0`, then applied at once: `f_80a9d330(MessageVoiceMgr, f_809d47c0(value))` and `f_809d4a10` | read |
| `f_809d47c0` | `VoiceLanguage_ToType(hash)` | A `VoiceLanguage` value to the region-language type of its files: English 1 (`USen`), Spanish_LatinAmerica 2, French_Canada 3, Portuguese_Brazil 4, Spanish_Spain 6, French_France 7, German 8, Italian 9; anything else, Japanese included, 0 (`JPja`) | read |
| `f_83300e50` | `SeqOneShotSetDefaultVoiceLanguage_Evaluate` | Slot 39 of `0x84bda7c8`: calls `f_809d48c0`. Only `Sequence/ConfirmationResetOption.module.ainb` uses the node (the options' reset) | read |
| `f_83300f20` | `SeqOneShotSetDefaultVoiceLanguage_Name` | The node's name (slot 19) | read |
| `f_80a9ca30` | `NpcVoiceResource_Update` | Slot 6 of `game::NpcVoiceResource` (`0x84bf1ec8`). On its first run (bit 2 of `+0x38` clear) it calls `f_809d48c0` and sets the bit. Each run, when `OptionData.VoiceLanguage` (0xa2dcdc9d) was written (`f_80a9d1c0`), it reads the value, maps it as `f_809d47c0` does (not found, or an unknown value: 0, `JPja`; French_Canada also sets bit 3), and calls `f_80a9d330` | read |
| `f_80a9d1c0` | `GameDataMgr_WasWritten(mgr, hash)` | True when the hash is in the game-data manager's list of values written (`+0x288` ring, count `+0x298`) or its struct-field set (`+0x2e8`) | read |
| `f_80a9d330` | `MessageVoiceMgr_SetLanguage(mgr, type)` | Looks the type's code hash (`0x83f85e90`) up in `VoiceSettingInfo`'s `LanguageMapping` (`mgr+0x6750`), copies the code it maps to, and handles `AltLanguageMapping` and `PartialReplace_…`. Types 0 to 17 only | read |
| `f_837b5670` | `MessageVoiceMgr_Init` | Slot 2 of `0x84c44f70`. Sets the manager's language from the region-language type (as `f_809d8f00` gets it) with `f_80a9d330` | read |
| `f_80d3f580` | `MessageVoiceMgr_Get` | The voice manager, through the GOT slot `0x84ca07d0` (making it the first time) | guess |
| `f_80d3f4b0` | `MessageVoiceMgr_OnLoad` | With the manager, loads `VoiceSettingInfo` (`f_809d8be0`) and sets `+0x211` | read |
| `f_809d8be0` | `VoiceSettingInfo_Load(mgr)` | Loads `Voice/VoiceSettingInfo.<variant>.byml` (the same variant string as the sound boot: `Product.111`) into `mgr+0x6728` | read |
| `f_837b67b0` | `MessageVoice_StreamPath` | Builds `content://Voice/Resource/Stream/<name>.bwav` (or "<NoSound>") | read |
| `f_80f292a0` | `MessageVoice_BarsPath` | Builds `Voice/Resource/<name>.bars` from an event's "Resources" | guess |
| `f_832fedb0` | `OptionData_Write` | "option_data": reads every `OptionData` field (`IsUseAmiibo`, `CameraRotateSpeed`, `DisplayMode`, `VoiceLanguage`, `LockOnType`, the ocarina's modes, `ControllerRumble`, …) into a record by name; the `VoiceLanguage` one by the same nine hashes | guess |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x83f0f980` | `RegionLanguageCodes` | The 18 region-language codes, 6 bytes each: `JPja USen USes USfr USpt EUen EUes EUfr EUde EUit EUpt EUnl EUru KRko CNzh TWzh EUpl THth` (type 0 to 17); `+0x6c` holds their offsets | read |
| `0x83f3a464` | `RegionLanguageTypeTable` | 13 languages by 7 regions, signed bytes: the type, or -1 where the pair is not a type | read |
| `0x83f3a440` | `RegionLanguageTypePairs` | For each type, its region and language (2 bytes) | read |
| `0x83f3a4c0` | `RegionDefaultOffsets` | For regions 1 to 6, the offset in `0x83f3a464` of the region's default (US and EU English, KRko, CNzh, TWzh, THth) | read |
| `0x83f85e90` | `RegionLanguageCodeHashes` | The 18 codes' murmur3 hashes, by type (the keys of `LanguageMapping`) | read |
| `0x83f2fd28` | `DefaultVoiceLanguageByType` | For types 1 to 17, the default `VoiceLanguage` value (a murmur3 hash): English for USen, EUen, EUnl, EUru, KRko, EUpl and THth; Japanese for EUpt, CNzh and TWzh; the type's own language for the other seven | read |
| `0x84ca07d0` | `got_MessageVoiceMgr` | The GOT slot `f_80d3f580` and `f_80a9ca30` reach the voice manager through | guess |
| `0x84c44f70` | `MessageVoiceMgr_vtable` | Slot 2 `f_837b5670`, slot 6 `f_80a9daa0`, slot 9 the name "MessageVoiceMgr" (`f_837b6bb0`) | read |
| `0x84bf1ec8` | `NpcVoiceResource_vtable` | `game::NpcVoiceResource`: slot 6 `f_80a9ca30`, slot 9 the name (`f_83339420`) | read |
| `0x84bda7c8` | `SeqOneShotSetDefaultVoiceLanguage_vtable` | Slot 19 `f_83300f20`, slot 39 `f_83300e50` | read |
| `0x84c38238` | `AudioFrameworkThread_vtable` | Slot 14 (`+0x70`) is the thread's run, `f_836f51d0` | read |
| `0x83f83998` | `MainAudioOut_ChannelMap` | The device sink's channel map, 0 1 2 3 4 5 | read |
| `0x84ea4e08` | `g_ResourceVariantHolder` | Holder (made by `f_80fb32f0`, 0x158 bytes) of the object whose `+8` gives the variant string at `+0x148` that the sound and voice paths use (`Product.111`) | guess |

The hashes (murmur3 of the names, as the game data's): `OptionData` 0x834d9d9e, `VoiceLanguage` 0x74b1edd9,
`OptionData.VoiceLanguage` 0xa2dcdc9d; the values Japanese 0x69b014ce, English 0x82591fa9, French_France 0x2498c397,
French_Canada 0x7d30fc45, German 0x515b9eeb, Spanish_Spain 0x33c26987, Spanish_LatinAmerica 0xa683b1fd, Italian
0x8d62c251, Portuguese_Brazil 0x1c3498eb. The option enum's names also list Russian (0x856a1920), which nothing maps.
**read**

## Structures

### Audio framework (`f_81043860`'s object)

| Offset | Field | Sure |
|---|---|---|
| `+0` | 1 while the thread is to run (set before the thread starts; the thread loops while it is 1) | read |
| `+4` | Manual rendering: 1 renders through `ExecuteAudioRendererRendering` and an `OutputStream` | read |
| `+0x44` | Started | read |
| `+0x88` | The heap | read |
| `+0x98`, `+0xa0` | The renderer's and the config's work buffers | read |
| `+0xa8` | The renderer's handle (16 bytes) | read |
| `+0xb8` | The `AudioRendererConfig` | read |
| `+0x130` | The renderer's system event | read |
| `+0x160` | `GetAudioRendererVoiceDropParameter`'s float | read |
| `+0x170` | "AudioFrameworkThread" | read |

### Voice manager (MessageVoiceMgr)

| Offset | Field | Sure |
|---|---|---|
| `+0x211` | 1 once `VoiceSettingInfo` is loaded | guess |
| `+0x6728` | The `VoiceSettingInfo` resource | read |
| `+0x6750` | Its root, where `LanguageMapping` is looked up | read |

### game::NpcVoiceResource

| Offset | Field | Sure |
|---|---|---|
| `+0x38` | Flags: bit 2, the default voice language set; bit 3, French_Canada (partial replacement) | read |
| `+0x3070` … `+0x3098` | A queue of requests and its mutex, served before the option check | guess |

## Notes

### The voice language, step by step

1. **The system language.** `GetDesiredLanguage` (LS: `language.txt` or `HOS_LANGUAGE`, 16 codes, default
   `en-US`) gives a region (`f_811f32f0`) and a language (`f_811f35f0`). **read**
2. **The region-language type.** The manager picks the type from the pair, kept to the types in
   `System/RegionLangMask.txt`. In the dump its first line lists 17 codes, all but `EUpt`. So European Portuguese becomes
   `EUen`. **read** (the code), the file
3. **The text** follows that type (fonts in `f_809d8d50`, messages). **read** (fonts), **guess** (messages)
4. **The voices** follow `OptionData.VoiceLanguage`. On its first update `NpcVoiceResource` sets the option to the type's
   default (`0x83f2fd28`) and applies it. After that it applies the option whenever the option is written. The options
   reset (`ConfirmationResetOption`) sets the default again. **read** Options screens can presumably also set it: the
   enum is in "option_data". **guess**
5. **The files.** The applied type's code is mapped through `LanguageMapping` (`EUen` to `USen`, the other eight to
   themselves). The voice files carry that code: 100 `.bars` and 940 stream `.bwav` for each code, except `USfr`, with 21
   streams. `AltLanguageMapping` (`USfr` to `EUfr`) and `PartialReplace_USfr` (22 entries) cover the rest. **read** (the
   code), the files

| `language.txt` | Type | Voices by default |
|---|---|---|
| en-US, en-GB, nl, ru, pl, ko (and th) | USen, EUen, EUnl, EUru, EUpl, KRko, THth | English (`USen`) |
| es-419, fr-CA, pt-BR | USes, USfr, USpt | their own |
| es, fr, de, it | EUes, EUfr, EUde, EUit | their own |
| ja, zh-Hans, zh-Hant (and pt) | JPja, CNzh, TWzh, EUen (EUpt is not in the mask) | Japanese, Japanese, Japanese, English |

`th` and `pt` are codes the game knows but LS's `language.txt` does not offer. **read**

### What #1460 points at

The reporter used `pt-BR`. That is region 1, language 6, so type 4, `USpt`. The default voice is Portuguese_Brazil,
mapped back to type 4. So the Portuguese dub at the start is what the code gives. **read** Two things would turn the
voices Japanese later and leave the text alone, because the text follows the region-language type and not the option.
**read**

- **The option written with Japanese.** That is `GameDataList`'s `DefaultValue` for `OptionData.VoiceLanguage`
  (`ResetTypeValue` 4, `SaveFileIndex` 1, `option.sav`). It would happen if the option data were reset to its
  defaults, or read back from an `option.sav` written before the first update set the default.
- **A value outside the nine** (Russian, 0, or the field not found). `f_80a9ca30` gives `JPja` for these.

Which write happens after Gohma was not found statically. What decides it is when `option.sav` is loaded and written
relative to the first update of `game::NpcVoiceResource`, and what resets `ResetTypeValue` 4. **guess**

### Frame rate

The audio frame is the renderer's (240 samples, 5 ms), run on "AudioFrameworkThread" from the renderer's event. LS's
mixer signals that event on its own clock (`renderer_main`), so the sound does not follow the game's frame rate. The game
side (the SoundModule job, [jobs.md](jobs.md)) runs once a game frame. **read**

## Open questions

- What writes `OptionData.VoiceLanguage` after the start of a game, and when `option.sav` is read relative to
  `f_80a9ca30`'s first run (#1460). Live: break on `f_80a9d330` and log its type and the caller.
- What `ResetTypeValue` 4 means, and which reset uses it.
- What `f_80a9d330` does for a type without a `LanguageMapping` entry (EUpt, EUnl, EUru, KRko, CNzh, TWzh, EUpl,
  THth); `MessageVoiceMgr_Init` can pass one.
- Whether the game ever adds the I3DL2 reverb (LS's is implemented) or only the plain Reverb the log shows.
- How much the missing Reverb, Delay and BiquadFilter effects and splitter biquads change the sound (rooms, underwater,
  `RoomReverbControllerSetting`, `EnableBgmInWaterFilter`).
- How the movies' sound reaches the output in LS, since the movie player's `OutputStream` path is never logged.
- The `%s` variant string's source object (`0x84ea4e08`) and how it becomes `Product.111`.
- Which object `f_801e9c80` and `f_806ab700` feed (the aux effects), now that they read no samples.
