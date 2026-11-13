# NVN calls: the game's boundary with the GPU

Every NVN function the game calls, where it reaches it from, and what each call costs under LS. The game asks the
NVN driver for 735 functions once at start-up, keeps a pointer to each in a table, and calls through that table from
about 7,500 places; LS answers each call with a C handler in `host/nvn_native.c` that records a small command, and
the Vulkan renderer replays the commands at the submit. The frame rate is CPU-bound, so what each call costs on the
game's threads matters. Looked at in November 2026 by static analysis of the binary and of LS's code (no runs).

**LS code:** `host/nvn_native.c` (every handler), `gpu/render/rx_nvn.h`
(the command records and the command buffer object), `host/hos_hostcall.c` and
`third_party/a64-kit/rt/a64_rt.c` (how a lifted call reaches a handler),
`host/rx_backend.c`, `gpu/vk/vk_replay.cpp` (the replay),
`nx2/standalone.py` (`nvn_exports`: the SDK's NVN functions hooked)
**See also:** [drawing.md](drawing.md) (the game's draw loops), [culling.md](culling.md) (the query and condition
calls), [textures.md](textures.md) (textures and pools), [jobs.md](jobs.md) (the threads that record), [rendering.md](rendering.md)

## In short

1. **One table of 735 pointers.** `f_812be290` asks `nvnDeviceGetProcAddress` for 735 names and stores each answer in its
   own 8-byte global, in the loader's order, from `0x84d66f00` to `0x84d685f0`. Code reaches a pointer through a GOT slot
   (between `0x84ca2dd0` and `0x84caee78`) or by its address directly. **read**
2. **285 of them are used, at 7,510 places in 988 functions.** The other 450 are loaded and never read, among them all 57
   DLSS functions (`nvnDlss*`) and the 13 dirty-tile-map ones. Three functions take over a quarter of the places:
   `nvnCommandBufferBindStorageBuffer` (712), `nvnBufferGetAddress` (690) and `nvnCommandBufferBindUniformBuffer` (666).
   Of the 100 hottest functions in play, 13 call NVN; the hottest are buffer and pool helpers (ranks 8, 9 and 10) and the
   recording job `f_80084de0` (rank 16). **read**
3. **The inline-command path is never taken under LS.** About 1,870 places first test `nvnPrivate0`
   (`0x85ff20a8`, in the SDK's zero-initialised data, imported through `0x84ca2dd8`): when it is set they write GPU
   methods straight into the command buffer's memory instead of calling. On the console the SDK's own
   `nvnDeviceGetProcAddress` (`f_855f6f80`) sets it when asked with a device. LS answers `nvnBootstrapLoader` and
   every NVN function the SDK exports itself, so that SDK code never runs, the flag stays 0, and every one of those places
   makes the call. Most are per-draw binds and queries: `BindUniformBuffer` ~499, `ResetCounter` ~296, `ReportCounter`
   ~285, `SetRenderEnable` ~246, `SetRenderEnableConditional` ~205. **read**
4. **Most calls are cheap records.** A call is the lifted indirect call through a per-site cache straight to the C handler,
   which appends a 16- to 48-byte record to the command buffer's host buffer with no lock. The costly ones are few:
   `EndRecording` (a copy of the whole recording, a lock), `CallCommands` (a lock and a copy of each recording called),
   `Initialize` and `Finalize` (a lock), `AddControlMemory` (a lock and a walk), `InvalidateMappedRange` (gives up the
   virtual core, may wait for the replay), `UpdateUniformBuffer` (copies its data), `SetRenderTargets` (a hash and a lock
   per target) and `QueueSubmitCommands`, which by default **replays the submission on the calling thread**. **read**
5. **Six functions the game uses have no handler.** `nvnCommandBufferReportValue` (12 places, in the clear helpers next to
   `ClearBuffer` and the conditional draws), `PushGraphicsConstants`, `SetColorReductionEnable`,
   `SetColorReductionThresholds`, `SetShadingRatePaletteMode` and `SetConservativeRasterMode` reach `unimplemented`: logged
   at the first call, answered 0, nothing recorded. **read** Whether `ReportValue`'s missing write matters was not checked.

## How the game reaches NVN

**Set-up.** The framework's graphics set-up (`f_8348be50`) asks `nvnBootstrapLoader` for `nvnDeviceInitialize` and
`nvnDeviceGetProcAddress` (and `nvnInstallGlobalDebugCallback` when the framework's byte `+0xae` is 1), fills the table
with no device (`f_812be290(0, getProcAddress)`), builds the device (`nvnDeviceBuilderSetDefaults` …), initialises a
0x3000-byte device object, and fills the table again with the device. It then reads the API version with
`nvnDeviceGetInteger`: major 231 and minor at least 40, or the rest of the set-up is skipped (at `0x8348c11c`, which
`host/nvn_native.c` names for its `devinfo` answer 231.40). **read**

**A call.** Almost every place loads the GOT slot, loads the pointer from it and calls (`blr`), or tail-calls (`br`) from
a small wrapper. 1,324 of the 7,510 places use the global's address directly instead of the GOT slot (the same pointer).
The game imports nothing else of NVN from the SDK: its only NVN imports are `nvnBootstrapLoader` and `nvnPrivate0`. **read**

**In LS.** `nvnBootstrapLoader` is answered by the host (`HLE(nvnBootstrapLoader)`, nvn_native.c:3208) and every
NVN function the SDK defines is hooked to the host's (`nvn_exports`, nx2/standalone.py), so the console driver never
runs. `nvnDeviceGetProcAddress` (nvn_native.c:186) gives each name a host call address once (`nvn_native_proc`,
nvn_native.c:3149: a binary search under an exclusive lock, at load only); a name with no handler gets one that reaches
`unimplemented` (nvn_native.c:3127). At a call, the lifted `blr` goes through `A64_SITE_CALL` (a64.h:145): a per-site
cache holding the handler, called as a plain function (`a64_lookup_host`, hos_hostcall.c), with the arguments read from
the guest registers. Before that change every NVN call went through `a64_call_miss` (a64_rt.c:32).

### The table

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84d66f00` | `g_NvnProcs` | The 735 proc pointers, 8 bytes each, in the loader's order (the first, `nvnDeviceBuilderSetDefaults`, is here; the last, `nvnCommandBufferGetAccelerationStructureVisualizationData`, at `0x84d685f0`). Written twice by `f_812be290` | read |
| `0x84ca2dd8` | `got_nvnPrivate0` | Main's import slot for the SDK's `nvnPrivate0` (`0x85ff20a8`, [culling.md](culling.md)); read at 1,871 places, always as a 32-bit flag | read |
| `0x85e2c158` | `sdkgot_nvnPrivate0` | The SDK's own GOT slot for `nvnPrivate0`, through which `f_855f6f80` sets it and `f_855f1750` clears it | read |
| `0x84d67ca0` | `pfn_nvnCommandBufferBindStorageBuffer` | The pointer to `nvnCommandBufferBindStorageBuffer` (GOT `0x84cadb40`; 712 places) | read |
| `0x84d67c78` | `pfn_nvnCommandBufferBindUniformBuffer` | The same for `nvnCommandBufferBindUniformBuffer` (GOT `0x84cadb38`; 666 places) | read |
| `0x84d67fd8` | `pfn_nvnCommandBufferBarrier` | The same for `nvnCommandBufferBarrier` (GOT `0x84cadaa8`; 338) | read |
| `0x84d67cd0` | `pfn_nvnCommandBufferBindTexture` | The same for `nvnCommandBufferBindTexture` (GOT `0x84cadb50`; 256) | read |
| `0x84d67df8` | `pfn_nvnCommandBufferDispatchCompute` | The same for `nvnCommandBufferDispatchCompute` (GOT `0x84cad910`; 252) | read |
| `0x84d673b8` | `pfn_nvnMemoryPoolGetFlags` | The same for `nvnMemoryPoolGetFlags` (GOT `0x84cad820`; 227) | read |
| `0x84d67c60` | `pfn_nvnCommandBufferBindProgram` | The same for `nvnCommandBufferBindProgram` (GOT `0x84cad9c8`; 212) | read |
| `0x84d67390` | `pfn_nvnMemoryPoolFlushMappedRange` | The same for `nvnMemoryPoolFlushMappedRange` (GOT `0x84cadbd0`; 181) | read |
| `0x84d67c10` | `pfn_nvnCommandBufferCallCommands` | The same for `nvnCommandBufferCallCommands` (GOT `0x84cadab0`; 105) | read |
| `0x84d67c50` | `pfn_nvnCommandBufferBindVertexAttribState` | The same for `nvnCommandBufferBindVertexAttribState` (GOT `0x84cad9b0`; 101) | read |
| `0x84d67c58` | `pfn_nvnCommandBufferBindVertexStreamState` | The same for `nvnCommandBufferBindVertexStreamState` (GOT `0x84cad9b8`; 100) | read |
| `0x84d67f50` | `pfn_nvnCommandBufferUpdateUniformBuffer` | The same for `nvnCommandBufferUpdateUniformBuffer` (GOT `0x84cadae8`; 94) | read |
| `0x84d67398` | `pfn_nvnMemoryPoolInvalidateMappedRange` | The same for `nvnMemoryPoolInvalidateMappedRange` (GOT `0x84cadbd8`; 67) | read |
| `0x84d67d50` | `pfn_nvnCommandBufferDrawElements` | The same for `nvnCommandBufferDrawElements` (GOT `0x84ca9c50`; 65) | read |
| `0x84d67c68` | `pfn_nvnCommandBufferBindVertexBuffer` | The same for `nvnCommandBufferBindVertexBuffer` (GOT `0x84cad9d8`; 52) | read |
| `0x84d66f50` | `pfn_nvnDeviceGetTextureHandle` | The same for `nvnDeviceGetTextureHandle` (GOT `0x84cadb48`; 49) | read |
| `0x84d67ce0` | `pfn_nvnCommandBufferBindTexturesDeferred` | The same for `nvnCommandBufferBindTexturesDeferred` (GOT `0x84ca2de0`; 34) | read |
| `0x84d67d90` | `pfn_nvnCommandBufferSetGeometryProcessingMode` | The same for `nvnCommandBufferSetGeometryProcessingMode` (GOT `0x84cae798`; 28) | read |
| `0x84d67d98` | `pfn_nvnCommandBufferDrawMeshTasks` | The same for `nvnCommandBufferDrawMeshTasks` (GOT `0x84cae7a0`; 14) | read |
| `0x84d67c00` | `pfn_nvnCommandBufferBeginRecording` | The same for `nvnCommandBufferBeginRecording` (GOT `0x84cad900`; 17) | read |
| `0x84d67c08` | `pfn_nvnCommandBufferEndRecording` | The same for `nvnCommandBufferEndRecording` (GOT `0x84cad908`; 19) | read |
| `0x84d67120` | `pfn_nvnQueueSubmitCommands` | The same for `nvnQueueSubmitCommands` (GOT `0x84caddd0`; 11) | read |
| `0x84d67f68` | `pfn_nvnCommandBufferReportValue` | The same for `nvnCommandBufferReportValue` (GOT `0x84cae860`; 12): no host handler | read |

The pointers for `ReportCounter`, `ResetCounter`, `SetRenderEnable`, `SetRenderEnableConditional` and `BufferGetAddress`
have rows on [culling.md](culling.md); `MemoryPoolMapVirtual`, `TextureInitialize`, `TexturePoolRegisterTexture`,
`TextureFinalize`, `TextureWriteTexels` and `CopyBufferToTexture` on [textures.md](textures.md). Every other used pointer
is in [All the NVN functions the game uses](#all-the-nvn-functions-the-game-uses) below.

## The inline-command path

NVN's headers let a caller write a few common commands straight into the command buffer's memory instead of calling
the driver. The game was built with them: before such a call it reads `nvnPrivate0`, and when that is non-zero and the
command buffer's word at `+0x18` is 0 it compares the write pointer at `+0` plus the command's size with the end at `+8`;
when the command fits it writes the GPU methods there (three or five 32-bit words for a uniform-buffer bind, eight for a
counter report) and moves the pointer on. Otherwise it calls through the table. It also sets the byte at `+0x10` to 1
before many of these calls. **read** (1,414 of the 1,871 places read the pointer pair at `+0`/`+8`)

**On the console** the flag is set by the SDK's `nvnDeviceGetProcAddress` (`f_855f6f80`): each call stores 1 when it is
given a device and two of the driver's own globals agree (`[0x85ff20c0]` non-zero, and the value behind `0x85e2cd98`
non-zero and equal to `0x85e2cda0`), else 0. The game's second table fill, with the device, is the call that sets it.
`f_855f1750` clears it. **read** (what the two globals mean: guess)

**Under LS** neither runs: `nvnBootstrapLoader` and every SDK NVN function are the host's, and the host's
`nvnDeviceGetProcAddress` writes nothing there. The flag is in the SDK's zero-initialised data, so it reads 0, and every
guarded place takes the call. **read** Even if it were set, the fit test would fail: LS's command buffer object
holds its magic (`CMDB`) and recording flag at `+0` and its slot number at `+8` (rx_nvn.h, `nvn_cmd`), so `+0` plus the
size is always past `+8`. (rx_nvn.h's comment says the write pointer is at `+0x20`/`+0x28`, from another game; in this
one it is at `+0`/`+8`.) **read**

What each flag test guards, found by following its branch to the call it falls back to (1,639 places), or else by the
next command-buffer call after it:

| Guarded call | Places with the test | All places | Note |
|---|---|---|---|
| `nvnCommandBufferBindUniformBuffer` | ~499 | 666 | Every place that reaches it through its GOT slot; the 167 direct ones are not guarded |
| `nvnCommandBufferResetCounter` | ~296 | 315 | The z-prepass queries ([culling.md](culling.md)) |
| `nvnCommandBufferReportCounter` | ~285 | 361 | The same |
| `nvnCommandBufferSetRenderEnable` | ~246 | 303 | The conditional draws |
| `nvnCommandBufferSetRenderEnableConditional` | ~205 | 265 | The same |
| `nvnCommandBufferUpdateUniformBuffer` | ~81 | 94 | |
| `nvnCommandBufferDrawElements` | ~61 | 65 | |
| `nvnCommandBufferCallCommands` | ~59 | 105 | |
| `nvnCommandBufferBindVertexBuffer` | ~34 | 52 | |
| `nvnCommandBufferBindTexturesDeferred` | ~17 | 34 | |
| `nvnCommandBufferBeginRecording` | ~15 | 17 | |
| Others (state binds, stencil, mesh draws, barriers) | ~50 | | |
| Not attributed | ~24 | | |

So on the console about 1,800 places, nearly all of them per draw or per query, can skip the call; under LS each of
them is a host call and a record. **read** (counts approximate: a few places test the flag once for several calls)

## What a call costs in LS

**The common path.** A record goes through `rec()` (nvn_native.c:1682): `cmd_alloc` (1354) finds the command buffer's
host buffer by the slot number in the object (`cmd_host`, 1280: a table lookup and an owner check, no lock), checks room
(`cmd_reserve_rec`, 1335), writes an 8-byte header and zeroes the payload. When the buffer is full it doubles, copying
what it holds, from a free list kept under `pool_lock` (`pool_get`, 1217). A guest pointer argument is checked to lie in the guest
window (`gw`, 38). No record handler takes a lock in the common case. **read**

**The replay.** At `QueueSubmitCommands` (2353) the host checks each handle (`check_handle`, a shared lock), copies the
deferred binds' arrays (`resolve_deferred`, 2336: a walk over every record), and hands the recordings to the renderer.
With `RX_VK_ASYNC` off, the default, the replay runs **on the submitting game thread** (vk_replay.cpp:1863, `submit_q`
4015), with the thread moved to the core kept for the replay and its virtual core given up. The replay (`replay`,
vk_replay.cpp:724) copies most records into its state (`NvnState`) and bumps a generation when a binding changes; draws
and dispatches go to `draw()` and `dispatch()`. So a call's cost is paid twice on the CPU: once at record, once at replay. **read**

### The 30 most used

Sites are the places that load the pointer; the hottest caller is the hottest function (by `hot_order.txt` rank) that
has one of them. Line numbers are in host/nvn_native.c unless said.

| NVN function | Sites | Hottest caller (rank) | Handler | What it does at record time |
|---|---|---|---|---|
| `nvnCommandBufferBindStorageBuffer` | 712 | `f_8008fc00` (35) | `bind_buf`, 1962 | A 32-byte record. Replay: compare and store (vk_replay.cpp:1043) |
| `nvnBufferGetAddress` | 690 | `f_80084de0` (16) | 493 | No record: the buffer's pool's GPU address plus its offset, the pool pointer checked to be in the window |
| `nvnCommandBufferBindUniformBuffer` | 666 | `f_8008fc00` (35) | `bind_buf`, 1962 | A 32-byte record, as above |
| `nvnCommandBufferReportCounter` | 361 | `f_80084de0` (16) | 1763 | A 24-byte record, and an atomic exchange on a shared word on every call (the first-report-of-a-type log). Replay writes the count into guest memory (vk_replay.cpp:780) |
| `nvnCommandBufferBarrier` | 338 | `f_80a47660` (37) | 2093 | A 16-byte record. Replay ends the render pass and records a full barrier (vk_replay.cpp:1154) |
| `nvnCommandBufferResetCounter` | 315 | `f_80084de0` (16) | 1752 | A 24-byte record |
| `nvnCommandBufferSetRenderEnable` | 303 | `f_80084de0` (16) | 1900 | A 16-byte record |
| `nvnCommandBufferSetRenderEnableConditional` | 265 | `f_800955a0` (69) | 1903 | A 40-byte record; reads nothing unless `HOS_COND_RECORD` is set. Replay reads the two words (vk_replay.cpp:1112) |
| `nvnCommandBufferBindTexture` | 256 | `f_8008fc00` (35) | 2017, `bind_handles` 1986 | A 40-byte record (`watch_tex` is a static test when `HOS_NVN_WATCH_TEX` is unset) |
| `nvnCommandBufferDispatchCompute` | 252 | `f_80364080` (79) | 2089 | A 24-byte record |
| `nvnMemoryPoolGetFlags` | 227 | `f_8009bb80` (8) | 350 | No record: the pool object's flags |
| `nvnCommandBufferBindProgram` | 212 | `f_8008fc00` (35) | 1919 | A 24-byte record of the program object's address; the replay compares the whole program object (vk_replay.cpp:954) |
| `nvnMemoryPoolFlushMappedRange` | 181 | `f_8009bb80` (8) | 369 | Nothing: CPU writes are found by write watch |
| `nvnCommandBufferCallCommands` | 105 | `f_80084de0` (16) | 1666, `inline_recording` 1652 | For each handle: a shared lock and a hash lookup (`check_handle`, 1643), then a copy of the whole recording into this one |
| `nvnCommandBufferBindVertexAttribState` | 101 | `f_80255be0` (213) | 1935 | A record with 4 bytes an attribute (the game passes 16: 72 bytes) |
| `nvnCommandBufferBindVertexStreamState` | 100 | `f_80255be0` (213) | 1944 | A record with 8 bytes a stream (16: 136 bytes) |
| `nvnCommandBufferUpdateUniformBuffer` | 94 | `f_8026a9e0` (341) | 1789 | Copies the data (up to 64 KiB) into the record. The replay copies it again, into a list (vk_replay.cpp:914) |
| `nvnMemoryPoolInvalidateMappedRange` | 67 | `f_8009bb80` (8) | 370 | Gives up the virtual core and takes it back (rx_backend.c:434); the renderer may wait for the replay or the GPU when the GPU writes back into the range (vk_replay.cpp:2239) |
| `nvnCommandBufferDrawElements` | 65 | `f_80435bc0` (149) | 2086, `draw_idx` 2078 | A 40-byte record. Replay: `draw()` (vk_replay.cpp:1198) |
| `nvnCommandBufferBindVertexBuffer` | 52 | `f_80255be0` (213) | 1961, `bind_vbufs` 1953 | A 32-byte record |
| `nvnDeviceGetTextureHandle` | 49 | `f_8008fc00` (35) | 232 | No record: the texture and sampler ids combined |
| `nvnTextureViewSetDefaults` | 42 | `f_8008fc00` (35) | 769 | Zeroes the view object in guest memory |
| `nvnBufferFinalize` | 37 | `f_8009bb80` (8) | 465 | Zeroes the buffer object |
| `nvnCommandBufferBindImage` | 36 | `f_800662e0` (1565) | 2019 | A 40-byte record |
| `nvnCommandBufferBindTexturesDeferred` | 34 | `f_8008fc00` (35) | 2027 | Copies the handle array and keeps its address; the submit copies it again (`resolve_deferred`) |
| `nvnBufferBuilderSetDefaults` | 29 | `f_8009bb80` (8) | 441 | Clears the builder in guest memory |
| `nvnBufferBuilderSetDevice` | 28 | `f_8009bb80` (8) | 440 | One store |
| `nvnBufferBuilderSetStorage` | 28 | `f_8009bb80` (8) | 447 | Three stores |
| `nvnTextureViewSetLevels` | 28 | `f_80263b60` (449) | 770 | Two stores and a range check |
| `nvnCommandBufferSetGeometryProcessingMode` | 28 | `f_80084de0` (16) | 2684 | Nothing |

### Hot, with few sites

These have few places but are called from the hottest recording code (`f_80084de0`, rank 16, and its kind), or through
wrappers with many callers ([Functions](#functions)).

| NVN function | Sites | Handler | What it does at record time |
|---|---|---|---|
| `nvnCommandBufferInitialize` | 15 | 1311 | A slot from the host's table under the exclusive `cmd_lock` (`cmd_adopt`, 1263) |
| `nvnCommandBufferFinalize` | 15 | 1321 | Gives the slot back under the exclusive `cmd_lock` (`cmd_release`, 1289) |
| `nvnCommandBufferAddControlMemory` | 15 | 1538 | `release_copies` (1455): under the exclusive `copy_lock`, frees dead copies and walks the buckets of the range's 64 KiB blocks |
| `nvnCommandBufferBeginRecording` | 17 | 1552 | An 8-byte record |
| `nvnCommandBufferEndRecording` | 19 | 1597 | **Heavy**: copies the whole recording (`flatten`, 1565, a block from the free list under `pool_lock`), a `malloc`, a mark written into the game's control memory, an insert under the exclusive `copy_lock`; every 25,000 recordings a sweep of all 65,536 buckets under that lock |
| `nvnCommandBufferDrawMeshTasks` | 14 | 2678, `draw_mesh` 2663 | A 48-byte record and an atomic increment of a shared counter (its log) |
| `nvnCommandBufferWaitEvent` / `SignalEvent` | 25 / 17 | 1708 / 1692 | A record and an atomic increment of a shared counter (their logs) |
| `nvnCommandBufferBindBlendState` … `BindDepthStencilState` | 14-18 each | `bind_state`, 1924 | A 40-byte record, the state object copied |
| `nvnBlendStateSet*`, `nvnPolygonStateSet*`, `nvnDepthStencilStateSet*`, … | 14-17 each | 2239-2318 | No record: one or two stores into the state object in guest memory. Each is still a call; on the console these are calls too |
| `nvnCommandBufferSetViewport` / `SetScissor` / `SetDepthRange` | 15-17 | 1867 / 1872 / 1887 | A record each |
| `nvnCommandBufferGetCommandMemoryUsed` / `GetControlMemoryUsed` | 16 | 1549 / 1546 | No record: what the real driver would have used |
| `nvnCommandBufferSetRenderTargets` | 8 | 1832 | A large record, and per target `tex_copy` (2132): an FNV hash over the texture object and a lookup under the exclusive `pin_lock` |
| `nvnQueueSubmitCommands` | 11 | 2353 | The replay, on this thread by default (above) |
| `nvnSyncWait` | 6 | 1097 | When the fence is not done: gives up the virtual core and waits for the renderer |

**Handlers that do nothing** but still cost a call: `MemoryPoolFlushMappedRange` (181 places, hottest caller rank 8),
`BufferFlushMappedRange` (reached through `f_800a9510`, 62 callers), `SetGeometryProcessingMode`,
`SetCommandMemoryCallbackEnabled`, `SetRecordingMode`, `SetShaderScratchMemory`, `BindShadingRateImage`,
`ShadingRateImagePalette`, `SetStencilCullCriteria` and the `SetDebugLabel`s. **read**

## Per draw

The game's draw loops are on [drawing.md](drawing.md); this is only what they ask of NVN, read from the hottest
functions that draw. Four shapes:

- **Material binds, then recorded commands** (`f_8008fc00`, rank 35; `f_800955a0`, rank 69): `BindProgram`, the state
  objects (blend, colour, channel mask, depth-stencil, polygon: each built with three to six setter calls, then bound),
  `SetBlendColor`, `SetPolygonOffsetClamp`, `SetDepthClamp`, the stencil values, then runs of uniform and storage buffer
  binds (64 and 60 places in `f_8008fc00` alone, unrolled by stage and slot), `BindTexturesDeferred` and `BindTexture`,
  and `CallCommands` to run a recording made earlier. Under LS each is a host call, each bind a small record,
  and each `CallCommands` a copy of the recording it calls. **read**
- **Indexed draws** (`f_80435bc0`, rank 149): for each draw a few uniform-buffer binds (each behind the inline flag), a
  storage-buffer bind, `nvnDeviceGetTextureHandle` and `BindTexture` per texture (61 places), `BufferGetAddress` for the
  index buffer, then `DrawElements`, `DrawElementsBaseVertex` or `DrawElementsInstanced`: roughly 10 to 20 calls between two draws
  in the function's order. **read**
- **Classic vertex draws** (`f_80255be0`, rank 213; `f_8098bff0`, rank 288; `f_80299510`, rank 490): `BindProgram`,
  textures, six uniform-buffer binds, `BindVertexAttribState` and `BindVertexStreamState` (16 each), `BufferGetAddress` and
  `BindVertexBuffer`, then `DrawElements`. `f_80255be0` also makes a buffer object each time (`BufferFinalize`, the three
  builder setters, `BufferInitialize`): about 30 calls up to its first draw. **read**
- **Mesh shading** (`f_80084de0`, rank 16, on [jobs.md](jobs.md) as `ModuleJob_Dispatch`): this function makes and
  records command buffers itself (`Initialize`, `AddControlMemory`, `AddCommandMemory`, `BeginRecording` … `EndRecording`,
  `Finalize`), sets the viewport and scissor, builds and binds every state object, and between them `ResetCounter`,
  `ReportCounter`, `SetRenderEnable`, `SetGeometryProcessingMode`, `CallCommands` and `DrawMeshTasks`. Under LS it
  is the one hot function that reaches the costly handlers (`Initialize`/`Finalize` and `EndRecording` take locks;
  `EndRecording` and `CallCommands` copy). **read**

A z-prepass object adds `ResetCounter` and `ReportCounter` around its draw, and a conditional object `SetRenderEnable`
and `SetRenderEnableConditional` before it ([culling.md](culling.md)): 244 and 258 template copies of those loops, which
is where most of the counter and condition places are. **read**

## Functions

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_812be290` | `NvnProcs_Load(device, getProcAddress)` | 735 calls of `getProcAddress(device, name)`, each answer stored through its GOT slot into its global in `g_NvnProcs` (`0x84d66f00`…`0x84d685f0`). Called twice by `f_8348be50`, with no device and then with it | read |
| `f_8348be50` | `GameFramework_InitNvn` | The framework's NVN set-up: `nvnBootstrapLoader` for `nvnDeviceInitialize` and `nvnDeviceGetProcAddress` (and `nvnInstallGlobalDebugCallback` when `+0xae` is 1), the table without a device, the device builder, a 0x3000-byte device, `nvnDeviceInitialize`, the table with the device, then `nvnDeviceGetInteger`: API 231 and minor at least 40 (`0x8348c11c`) or it stops there. Also uses "Presentation Thread". Called from `nnMain` (`f_81222ce0`, [graphics.md](graphics.md)) | read |
| `f_855f6f80` | `NvnSdk_DeviceGetProcAddress(device, name)` | The SDK's `nvnDeviceGetProcAddress`: stores the inline-command flag `nvnPrivate0` (1 with a device and the driver's state as expected, else 0), then binary-searches 735 names after the "nvn" prefix. Never runs under LS | read |
| `f_855f1750` | `NvnSdk_ClearPrivate0` | Stores 0 into `nvnPrivate0` (the rest not read); three callers and a table word (`0x85e56dd8`). Likely the device's finalize | guess |
| `f_812c3470` | `NvnConv_Primitive(x)` | A table lookup (`0x83e79c60`): the game's primitive enum to NVN's | read |
| `f_812c3480` | `NvnConv_Stage(x)` | A table lookup (`0x83e79c9c`): the game's shader-stage enum to NVN's, for the bind wrappers: the game's stages 0–7 map to NVN's 0, 3, 4, 2, 1, 5, 8, 6 ([shaders.md](shaders.md)) | read |
| `f_812c3490` | `NvnConv_StageBits(x)` | Moves the bits of a stage mask into NVN's order, for `BindProgram` | read |
| `f_812c34e0` | `NvnConv_IndexType(x)` | A table lookup (`0x83e7a994`); `f_812c34f0` is a copy | read |
| `f_812c3500` | `NvnConv_BufferAddress(range)` | Returns its first argument: a buffer range's address as NVN's | read |
| `f_8002d440` | `DrawCtx_BindUniformBuffer(ctx, slot, stage, range, size)` | `nvnCommandBufferBindUniformBuffer` on the context's command buffer (`ctx+0xd0`), the stage converted; a tail call. Hot rank 504, 31 callers | read |
| `f_812be080` | `DrawCtx_BindStorageBuffer(ctx, slot, stage, range, size)` | The same for `nvnCommandBufferBindStorageBuffer`. Hot rank 1348, 17 callers | read |
| `f_812bd9d0` | `DrawCtx_BindProgram(ctx, program, stages)` | `nvnCommandBufferBindProgram` with the program at `program+8` and its stage mask (`+4`, or converted when `+1` bit 0 is set). Hot rank 574 | read |
| `f_80104de0` | `DrawCtx_BindTexture(ctx, slot, stage, texId, samplerId)` | `nvnDeviceGetTextureHandle` on `[[ctx+8]]`, then `nvnCommandBufferBindTexture` on `ctx+0xd0`. Hot rank 741, 61 callers | read |
| `f_8002a5f0` | `GpuBuffer_GetRange(buf, out)` | `nvnBufferGetAddress(buf+8)` into `out`, with 0 after it. Hot rank 857, 31 callers | read |
| `f_8008f840` | `DrawCtx_DrawElementsInstanced(ctx, prim, itype, …)` | `nvnCommandBufferDrawElementsInstanced` on `ctx+0xd0`, the primitive and index type converted. Hot rank 1141, 35 callers | read |
| `f_8353e5d0` | `CmdBuf_BindUniformBufferInline(_, cmd, stage, index, addr, size)` | Sets `cmd+0x10` to 1; with the inline flag set and a stage other than 5 or 8 writes the bind's methods into the command buffer when they fit, else tail-calls `nvnCommandBufferBindUniformBuffer`. 70 callers, not ranked | read |
| `f_809f0900` | `Mesh_DrawElements(mesh, ctx, _, instances)` | Nothing when the count `mesh+0x30` is 0; else the index buffer's address (`mesh+0x38`) and `DrawElements` (fewer than 2 instances) or `DrawElementsInstanced`, each behind the inline flag, on `[ctx+0xd8]`. 137 callers | read |
| `f_809f0a20` | `Mesh_BindVertexBuffers(mesh, ctx)` | For each of the `mesh+0x108` buffers in `mesh+0x110`: its address and size (`+0x120`), then `BindVertexBuffer`, behind the inline flag. 122 callers | read |
| `f_809f1180` | `Mesh_BindVertexStates(mesh, ctx)` | `BindVertexAttribState(16, mesh+0x11c)` and `BindVertexStreamState(16, mesh+0x160)`: always all 16. 69 callers | read |
| `f_809ebc00` | `Gfx_Barrier(obj, ctx, flags)` | `nvnCommandBufferBarrier` on `[ctx+0xd8]` with bits made from `[flags]` (and a device setting at `[[0x84ca2d60]+0x9cc]`), when `obj+0x10` is set. 120 callers | read |
| `f_835c3140` | `DrawCtx_SetGeometryProcessingMode(ctx, item)` | `SetGeometryProcessingMode` only when `item+0x69e` differs from the one kept at `ctx+0x3b`. 252 callers, all in 0x8366d9ec–0x836a8dd4, none ranked | read |
| `f_83607f60` | `DrawCtx_DrawElementsIndirect(list, ctx, index, slot)` | `DrawElementsIndirect` (primitive 4) with the arguments in the list's entry; 256 callers in the same range, none ranked | read |
| `f_8359f530` | `DrawCtx_DrawMeshTasks(obj, ctx, …)` | By `obj+0x69f`: 0, another path (`f_8029fef0`); 1, `DrawMeshTasks(0, n)`; 2, `DrawMeshTasks(0, (n + 31) / 32)`, with n at `obj+0x6a4`. 66 callers | read |
| `f_800a8df0` | `GpuBuffer_Map(buf)` | `nvnBufferMap(buf+8)`. Hot rank 1623, 81 callers | read |
| `f_800a9510` | `GpuBuffer_Flush(buf, offset, size)` | `nvnBufferFlushMappedRange(buf+8, …)` when `buf+1` bit 1 is set; a no-op handler in LS. Hot rank 2331, 62 callers | read |
| `f_80527e90` | `Queue_SubmitOne(queue, handles)` | `f_80527f00`, then `nvnQueueSubmitCommands(queue, 1, handles)`. Hot rank 452 | read |
| `f_80527f00` | `Queue_SubmitPending` | `nvnQueueSubmitCommands` and `nvnQueueFlush`. Hot rank 427 | guess |
| `f_805902c0` | `Queue_SubmitFenced` | `nvnQueueWaitSync`, `nvnQueueSubmitCommands`, `nvnQueueFenceSync`, `nvnQueueFlush`, `nvnEventGetValue`, in that order. Hot rank 261 | read |

## Notes

- `f_80527f00` sorts the frame's queued command buffers into order, then makes one `QueueSubmitCommands` and a `QueueFlush` ([drawing.md](drawing.md)). **read**

- **How the places were counted.** Every ADRP+LDR pair that forms a pointer's GOT slot (outside the loader) or the
  pointer's own address. A function is bounded by the next known entry (BL targets, code addresses in data, and
  `hot_order.txt`), so a large function may take in a neighbour. A caller's rank is its function's, not the call's: the
  call may sit in a cold branch. Calls made through wrappers count once, at the wrapper (`nvnBufferMap` has 2 places but
  `f_800a8df0` has 81 callers). **read**
- **The hottest NVN callers** are buffer and pool helpers, not draws: `f_8009bb80` (rank 8: `MemoryPoolGetFlags` 13,
  `InvalidateMappedRange` 12, buffer making 6 each), `f_80021ca0` (rank 9, the LOD module's worker on [animation.md](animation.md): `GetFlags` and
  `InvalidateMappedRange`, 6 each, for the actors' occlusion sphere queries; it uses the names `mLastTransform` and
  `mPrevTransform`) and `f_812342c0` (rank 10). They pair `GetFlags` with a
  flush or invalidate, as `SphereQuery_ReadResult` does when a pool is CPU-cached ([culling.md](culling.md)). Under
  LS the flush is free and each invalidate gives up and takes back the virtual core. **read** How often the
  invalidates run in play was not measured.
- **`f_806668b0`** (`Draw_Wait` on [jobs.md](jobs.md)) ends the frame's recording (`ReportCounter`, `EndRecording`,
  `Finalize`), then `QueueFlush`, `QueueSubmitCommands`, `QueueFenceSync` and `nvnSyncWait`: the wait is the host's
  `nvnSyncWait` waiting for the renderer. **read**
- The host's comments give scale: NVN calls are "tens of thousands a frame" on the recording threads (a64_rt.c:32), and
  recording copies "~13k a frame" (nvn_native.c:1197). Neither was measured for this page.

## All the NVN functions the game uses

The 285 used, by places. Pointer is the global in `g_NvnProcs`; GOT is the slot that holds its address.

| NVN function | GOT slot | Pointer | Sites | Functions | Hottest caller (rank) |
|---|---|---|---|---|---|
| `nvnCommandBufferBindStorageBuffer` | `0x84cadb40` | `0x84d67ca0` | 712 | 45 | `f_8008fc00` (35) |
| `nvnBufferGetAddress` | `0x84ca9c40` | `0x84d674a8` | 690 | 379 | `f_80084de0` (16) |
| `nvnCommandBufferBindUniformBuffer` | `0x84cadb38` | `0x84d67c78` | 666 | 39 | `f_8008fc00` (35) |
| `nvnCommandBufferReportCounter` | `0x84cadad8` | `0x84d67f58` | 361 | 266 | `f_80084de0` (16) |
| `nvnCommandBufferBarrier` | `0x84cadaa8` | `0x84d67fd8` | 338 | 104 | `f_80a47660` (37) |
| `nvnCommandBufferResetCounter` | `0x84cadad0` | `0x84d67f60` | 315 | 248 | `f_80084de0` (16) |
| `nvnCommandBufferSetRenderEnable` | `0x84cae868` | `0x84d67f70` | 303 | 250 | `f_80084de0` (16) |
| `nvnCommandBufferSetRenderEnableConditional` | `0x84cae870` | `0x84d67f78` | 265 | 245 | `f_800955a0` (69) |
| `nvnCommandBufferBindTexture` | `0x84cadb50` | `0x84d67cd0` | 256 | 48 | `f_8008fc00` (35) |
| `nvnCommandBufferDispatchCompute` | `0x84cad910` | `0x84d67df8` | 252 | 55 | `f_80364080` (79) |
| `nvnMemoryPoolGetFlags` | `0x84cad820` | `0x84d673b8` | 227 | 149 | `f_8009bb80` (8) |
| `nvnCommandBufferBindProgram` | `0x84cad9c8` | `0x84d67c60` | 212 | 42 | `f_8008fc00` (35) |
| `nvnMemoryPoolFlushMappedRange` | `0x84cadbd0` | `0x84d67390` | 181 | 136 | `f_8009bb80` (8) |
| `nvnCommandBufferCallCommands` | `0x84cadab0` | `0x84d67c10` | 105 | 50 | `f_80084de0` (16) |
| `nvnCommandBufferBindVertexAttribState` | `0x84cad9b0` | `0x84d67c50` | 101 | 67 | `f_80255be0` (213) |
| `nvnCommandBufferBindVertexStreamState` | `0x84cad9b8` | `0x84d67c58` | 100 | 67 | `f_80255be0` (213) |
| `nvnCommandBufferUpdateUniformBuffer` | `0x84cadae8` | `0x84d67f50` | 94 | 28 | `f_8026a9e0` (341) |
| `nvnMemoryPoolInvalidateMappedRange` | `0x84cadbd8` | `0x84d67398` | 67 | 31 | `f_8009bb80` (8) |
| `nvnCommandBufferDrawElements` | `0x84ca9c50` | `0x84d67d50` | 65 | 47 | `f_80435bc0` (149) |
| `nvnCommandBufferBindVertexBuffer` | `0x84cad9d8` | `0x84d67c68` | 52 | 38 | `f_80255be0` (213) |
| `nvnDeviceGetTextureHandle` | `0x84cadb48` | `0x84d66f50` | 49 | 32 | `f_8008fc00` (35) |
| `nvnTextureViewSetDefaults` | `0x84cada10` | `0x84d67640` | 42 | 31 | `f_8008fc00` (35) |
| `nvnBufferFinalize` | `0x84cad828` | `0x84d67498` | 37 | 31 | `f_8009bb80` (8) |
| `nvnCommandBufferBindImage` | `0x84cadb68` | `0x84d67ce8` | 36 | 29 | `f_800662e0` (1565) |
| `nvnCommandBufferBindTexturesDeferred` | `0x84ca2de0` | `0x84d67ce0` | 34 | 4 | `f_8008fc00` (35) |
| `nvnBufferBuilderSetDefaults` | `0x84cad800` | `0x84d67458` | 29 | 23 | `f_8009bb80` (8) |
| `nvnBufferBuilderSetDevice` | `0x84cad808` | `0x84d67450` | 28 | 23 | `f_8009bb80` (8) |
| `nvnBufferBuilderSetStorage` | `0x84cad810` | `0x84d67460` | 28 | 23 | `f_8009bb80` (8) |
| `nvnCommandBufferSetGeometryProcessingMode` | `0x84cae798` | `0x84d67d90` | 28 | 20 | `f_80084de0` (16) |
| `nvnTextureViewSetLevels` | `0x84cada18` | `0x84d67648` | 28 | 24 | `f_80263b60` (449) |
| `nvnBufferInitialize` | `0x84cad818` | `0x84d67488` | 27 | 22 | `f_8009bb80` (8) |
| `nvnCommandBufferWaitEvent` | `0x84caea18` | `0x84d68168` | 25 | 14 | `f_80a47660` (37) |
| `nvnMemoryPoolFinalize` | `0x84cadbc0` | `0x84d67380` | 24 | 22 | none ranked |
| `nvnTextureBuilderSetDefaults` | `0x84cad868` | `0x84d674e8` | 24 | 16 | `f_80264bb0` (1365) |
| `nvnTextureBuilderSetDevice` | `0x84cad860` | `0x84d674e0` | 24 | 17 | `f_80264bb0` (1365) |
| `nvnTextureBuilderSetFormat` | `0x84cad888` | `0x84d67538` | 24 | 17 | `f_80264bb0` (1365) |
| `nvnTextureBuilderGetStorageSize` | `0x84cae120` | `0x84d67558` | 23 | 17 | `f_8005ed00` (54) |
| `nvnTextureBuilderSetTarget` | `0x84cad890` | `0x84d674f8` | 23 | 17 | `f_80264bb0` (1365) |
| `nvnMemoryPoolBuilderSetFlags` | `0x84cadff0` | `0x84d67348` | 22 | 18 | `f_8005ed00` (54) |
| `nvnMemoryPoolBuilderSetStorage` | `0x84cadfe8` | `0x84d67340` | 22 | 18 | `f_8005ed00` (54) |
| `nvnDeviceGetInteger` | `0x84cad7f8` | `0x84d66f38` | 21 | 19 | `f_80264bb0` (1365) |
| `nvnMemoryPoolBuilderSetDefaults` | `0x84cadfe0` | `0x84d67338` | 21 | 18 | `f_8005ed00` (54) |
| `nvnMemoryPoolBuilderSetDevice` | `0x84cadba8` | `0x84d67330` | 21 | 18 | `f_8005ed00` (54) |
| `nvnMemoryPoolInitialize` | `0x84cadbb0` | `0x84d67370` | 21 | 17 | `f_8005ed00` (54) |
| `nvnCommandBufferEndRecording` | `0x84cad908` | `0x84d67c08` | 19 | 19 | `f_80084de0` (16) |
| `nvnTextureBuilderSetStorage` | `0x84cad8a0` | `0x84d67570` | 19 | 17 | `f_8005ed00` (54) |
| `nvnTextureViewSetSwizzle` | `0x84cae208` | `0x84d67660` | 19 | 11 | `f_8008fc00` (35) |
| `nvnCommandBufferBindPolygonState` | `0x84cad1e8` | `0x84d67c40` | 18 | 13 | `f_80084de0` (16) |
| `nvnTextureInitialize` | `0x84cad8b0` | `0x84d676b0` | 18 | 17 | `f_8005ed00` (54) |
| `nvnCommandBufferBeginRecording` | `0x84cad900` | `0x84d67c00` | 17 | 17 | `f_80084de0` (16) |
| `nvnCommandBufferSetPolygonOffsetClamp` | `0x84cad1f0` | `0x84d67eb8` | 17 | 12 | `f_80084de0` (16) |
| `nvnCommandBufferSetScissor` | `0x84cada90` | `0x84d67e20` | 17 | 13 | `f_80084de0` (16) |
| `nvnCommandBufferSignalEvent` | `0x84caea20` | `0x84d68170` | 17 | 7 | `f_80660320` (41) |
| `nvnPolygonStateSetCullFace` | `0x84cad1c8` | `0x84d67a88` | 17 | 12 | `f_80084de0` (16) |
| `nvnPolygonStateSetFrontFace` | `0x84cad1d0` | `0x84d67a90` | 17 | 12 | `f_80084de0` (16) |
| `nvnPolygonStateSetPolygonMode` | `0x84cad1d8` | `0x84d67a98` | 17 | 12 | `f_80084de0` (16) |
| `nvnPolygonStateSetPolygonOffsetEnables` | `0x84cad1e0` | `0x84d67aa0` | 17 | 12 | `f_80084de0` (16) |
| `nvnTextureBuilderGetStorageAlignment` | `0x84cad8a8` | `0x84d67560` | 17 | 11 | `f_80547f20` (3589) |
| `nvnCommandBufferGetCommandMemoryUsed` | `0x84cae6d0` | `0x84d67bd0` | 16 | 16 | `f_80084de0` (16) |
| `nvnCommandBufferGetControlMemoryUsed` | `0x84cae6e8` | `0x84d67be8` | 16 | 16 | `f_80084de0` (16) |
| `nvnCommandBufferAddCommandMemory` | `0x84cad8f0` | `0x84d67bb8` | 15 | 15 | `f_80084de0` (16) |
| `nvnCommandBufferAddControlMemory` | `0x84cad8f8` | `0x84d67bc0` | 15 | 15 | `f_80084de0` (16) |
| `nvnCommandBufferBindChannelMaskState` | `0x84cad1c0` | `0x84d67c28` | 15 | 12 | `f_80084de0` (16) |
| `nvnCommandBufferBindDepthStencilState` | `0x84cad988` | `0x84d67c48` | 15 | 11 | `f_80084de0` (16) |
| `nvnCommandBufferFinalize` | `0x84cad8e8` | `0x84d67b88` | 15 | 15 | `f_80084de0` (16) |
| `nvnCommandBufferInitialize` | `0x84cad8c0` | `0x84d67b80` | 15 | 15 | `f_80084de0` (16) |
| `nvnCommandBufferSetMemoryCallback` | `0x84cad8c8` | `0x84d67b98` | 15 | 15 | `f_80084de0` (16) |
| `nvnCommandBufferSetMemoryCallbackData` | `0x84cad8d0` | `0x84d67ba0` | 15 | 15 | `f_80084de0` (16) |
| `nvnCommandBufferSetStencilMask` | `0x84cad998` | `0x84d67e90` | 15 | 11 | `f_80084de0` (16) |
| `nvnCommandBufferSetViewport` | `0x84cae7d8` | `0x84d67e08` | 15 | 12 | `f_80084de0` (16) |
| `nvnDepthStencilStateSetDepthTestEnable` | `0x84cae600` | `0x84d67ad0` | 15 | 11 | `f_80084de0` (16) |
| `nvnDepthStencilStateSetDepthWriteEnable` | `0x84cae608` | `0x84d67ad8` | 15 | 11 | `f_80084de0` (16) |
| `nvnDepthStencilStateSetStencilFunc` | `0x84cae620` | `0x84d67af0` | 15 | 11 | `f_80084de0` (16) |
| `nvnDepthStencilStateSetStencilOp` | `0x84cae628` | `0x84d67af8` | 15 | 11 | `f_80084de0` (16) |
| `nvnDepthStencilStateSetStencilTestEnable` | `0x84cae618` | `0x84d67ae8` | 15 | 11 | `f_80084de0` (16) |
| `nvnTextureBuilderSetSize2D` | `0x84cae0f0` | `0x84d67520` | 15 | 9 | `f_80547f20` (3589) |
| `nvnBlendStateSetDefaults` | `0x84cae440` | `0x84d678e8` | 14 | 11 | `f_80084de0` (16) |
| `nvnChannelMaskStateSetChannelMask` | `0x84cad1b8` | `0x84d679a0` | 14 | 11 | `f_80084de0` (16) |
| `nvnCommandBufferBindBlendState` | `0x84cad970` | `0x84d67c20` | 14 | 11 | `f_80084de0` (16) |
| `nvnCommandBufferBindColorState` | `0x84cad978` | `0x84d67c30` | 14 | 11 | `f_80084de0` (16) |
| `nvnCommandBufferDrawArrays` | `0x84ca9c90` | `0x84d67d48` | 14 | 14 | `f_8066bec0` (2109) |
| `nvnCommandBufferDrawMeshTasks` | `0x84cae7a0` | `0x84d67d98` | 14 | 12 | `f_80084de0` (16) |
| `nvnCommandBufferDrawMeshTasksIndirect` | `0x84cae7a8` | `0x84d67da0` | 14 | 14 | `f_801a46a0` (1896) |
| `nvnCommandBufferSetDepthRange` | `0x84cad9e0` | `0x84d67e30` | 14 | 12 | `f_80084de0` (16) |
| `nvnCommandBufferSetStencilRef` | `0x84cad9a0` | `0x84d67e98` | 14 | 11 | `f_80084de0` (16) |
| `nvnCommandBufferSetStencilValueMask` | `0x84cad990` | `0x84d67e88` | 14 | 11 | `f_80084de0` (16) |
| `nvnTextureGetHeight` | `0x84cada78` | `0x84d676f8` | 14 | 13 | `f_81503720` (1776) |
| `nvnTextureGetWidth` | `0x84cada70` | `0x84d676f0` | 14 | 13 | `f_81503720` (1776) |
| `nvnBlendStateSetBlendEquation` | `0x84cae458` | `0x84d67900` | 13 | 10 | `f_80084de0` (16) |
| `nvnBlendStateSetBlendFunc` | `0x84cae450` | `0x84d678f8` | 13 | 10 | `f_80084de0` (16) |
| `nvnBlendStateSetBlendTarget` | `0x84cae448` | `0x84d678f0` | 13 | 10 | `f_80084de0` (16) |
| `nvnColorStateSetBlendEnable` | `0x84cae4c0` | `0x84d67968` | 13 | 10 | `f_80084de0` (16) |
| `nvnColorStateSetDefaults` | `0x84cae4b8` | `0x84d67960` | 13 | 10 | `f_80084de0` (16) |
| `nvnColorStateSetLogicOp` | `0x84cae4c8` | `0x84d67970` | 13 | 10 | `f_80084de0` (16) |
| `nvnCommandBufferClearBuffer` | `0x84cada50` | `0x84d67f30` | 13 | 5 | `f_80265760` (594) |
| `nvnCommandBufferClearColor` | `0x84cae7c0` | `0x84d67dd8` | 13 | 6 | `f_80263b60` (449) |
| `nvnCommandBufferSetCommandMemoryCallbackEnabled` | `0x84cae6b8` | `0x84d67ba8` | 13 | 13 | `f_80084de0` (16) |
| `nvnCommandBufferSetDepthClamp` | `0x84ca2dd0` | `0x84d67ed8` | 13 | 10 | `f_80084de0` (16) |
| `nvnCommandBufferSetRecordingMode` | `0x84cad8d8` | `0x84d67bf8` | 13 | 11 | `f_80084de0` (16) |
| `nvnDepthStencilStateSetDepthFunc` | `0x84cae610` | `0x84d67ae0` | 13 | 10 | `f_80084de0` (16) |
| `nvnDeviceGetImageHandle` | `0x84cadb60` | `0x84d66f60` | 13 | 8 | `f_800662e0` (1565) |
| `nvnTextureViewSetLayers` | `0x84cae1f8` | `0x84d67650` | 13 | 12 | `f_80263b60` (449) |
| `nvnCommandBufferReportValue` | `0x84cae860` | `0x84d67f68` | 12 | 4 | `f_80265760` (594) |
| `nvnCommandBufferSetBlendColor` | `0x84cad980` | `0x84d67ea0` | 12 | 9 | `f_80084de0` (16) |
| `nvnCommandBufferSetTiledCacheAction` | `0x84cae7e8` | `0x84d67e48` | 12 | 7 | `f_8074b190` (2004) |
| `nvnTextureBuilderSetFlags` | `0x84cad898` | `0x84d674f0` | 12 | 12 | `f_80264bb0` (1365) |
| `nvnTexturePoolRegisterTexture` | `0x84cae040` | `0x84d673d8` | 12 | 12 | `f_800662e0` (1565) |
| `nvnQueueSubmitCommands` | `0x84caddd0` | `0x84d67120` | 11 | 11 | `f_805902c0` (261) |
| `nvnTextureFinalize` | `0x84cad8b8` | `0x84d676c0` | 11 | 11 | `f_80557420` (759) |
| `nvnTextureViewSetFormat` | `0x84cae200` | `0x84d67658` | 11 | 9 | `f_80285b00` (1503) |
| `nvnTextureBuilderSetLevels` | `0x84cae100` | `0x84d67530` | 10 | 10 | `f_80264bb0` (1365) |
| `nvnCommandBufferCopyTextureToTexture` | `0x84cada20` | `0x84d67f10` | 9 | 8 | `f_80289590` (1279) |
| `nvnProgramFinalize` | `0x84cadfb0` | `0x84d67300` | 9 | 9 | none ranked |
| `nvnCommandBufferDrawElementsInstanced` | `0x84ca9c48` | `0x84d67d68` | 8 | 7 | `f_80435bc0` (149) |
| `nvnCommandBufferSetRenderTargets` | `0x84cad9d0` | `0x84d67fa0` | 8 | 7 | `f_80265760` (594) |
| `nvnMemoryPoolMap` | `0x84cadbc8` | `0x84d67388` | 8 | 6 | none ranked |
| `nvnQueueFlush` | `0x84caddd8` | `0x84d67128` | 8 | 8 | `f_805902c0` (261) |
| `nvnTextureBuilderSetDepth` | `0x84cad880` | `0x84d67510` | 8 | 8 | `f_80264bb0` (1365) |
| `nvnTextureBuilderSetDepthStencilMode` | `0x84cae118` | `0x84d67550` | 8 | 8 | `f_80264bb0` (1365) |
| `nvnTextureBuilderSetHeight` | `0x84cad878` | `0x84d67508` | 8 | 8 | `f_80264bb0` (1365) |
| `nvnTextureBuilderSetSamples` | `0x84cae108` | `0x84d67540` | 8 | 8 | `f_80264bb0` (1365) |
| `nvnTextureBuilderSetStride` | `0x84cae140` | `0x84d67588` | 8 | 8 | `f_80264bb0` (1365) |
| `nvnTextureBuilderSetSwizzle` | `0x84cae110` | `0x84d67548` | 8 | 8 | `f_80264bb0` (1365) |
| `nvnTextureBuilderSetWidth` | `0x84cad870` | `0x84d67500` | 8 | 8 | `f_80264bb0` (1365) |
| `nvnProgramBuilderSetDevice` | `0x84cadf28` | `0x84d67278` | 7 | 7 | none ranked |
| `nvnProgramBuilderSetShaders` | `0x84cadf58` | `0x84d672a8` | 7 | 7 | none ranked |
| `nvnProgramInitializeFromBuilder` | `0x84cadfa0` | `0x84d672f0` | 7 | 7 | none ranked |
| `nvnQueueFinalize` | `0x84caddc0` | `0x84d67110` | 7 | 2 | none ranked |
| `nvnTextureGetFormat` | `0x84cada28` | `0x84d67710` | 7 | 6 | `f_81503720` (1776) |
| `nvnBufferMap` | `0x84cad830` | `0x84d674a0` | 6 | 6 | `f_800a8df0` (1623) |
| `nvnCommandBufferClearDepthStencil` | `0x84cada98` | `0x84d67df0` | 6 | 6 | `f_80263b60` (449) |
| `nvnCommandBufferPushComputeConstants` | `0x84caed00` | `0x84d68478` | 6 | 6 | `f_81503720` (1776) |
| `nvnCommandBufferSetTiledCacheTileSize` | `0x84cae7f0` | `0x84d67e50` | 6 | 6 | `f_8074b190` (2004) |
| `nvnCommandBufferShadingRateImagePalette` | `0x84caea30` | `0x84d68180` | 6 | 5 | `f_80660320` (41) |
| `nvnMemoryPoolGetBufferAddress` | `0x84cae010` | `0x84d673a0` | 6 | 6 | none ranked |
| `nvnQueueFinish` | `0x84cadde0` | `0x84d67130` | 6 | 6 | `f_80547f20` (3589) |
| `nvnSamplerBuilderSetDefaults` | `0x84cae308` | `0x84d677b0` | 6 | 6 | none ranked |
| `nvnSamplerBuilderSetDevice` | `0x84cae300` | `0x84d677a8` | 6 | 6 | none ranked |
| `nvnSamplerFinalize` | `0x84cae3d0` | `0x84d67878` | 6 | 6 | none ranked |
| `nvnSamplerInitialize` | `0x84cae3c8` | `0x84d67870` | 6 | 6 | none ranked |
| `nvnSyncWait` | `0x84cae9a0` | `0x84d680f0` | 6 | 4 | `f_806668b0` (374) |
| `nvnTextureGetFlags` | `0x84cae260` | `0x84d676e0` | 6 | 6 | `f_800662e0` (1565) |
| `nvnTextureGetTarget` | `0x84cada08` | `0x84d676e8` | 6 | 6 | `f_802f35a0` (3514) |
| `nvnTextureViewSetTarget` | `0x84cae218` | `0x84d67670` | 6 | 6 | `f_800662e0` (1565) |
| `nvnBufferFlushMappedRange` | `0x84cad838` | `0x84d674b0` | 5 | 5 | `f_800a9510` (2331) |
| `nvnCommandBufferDispatchComputeIndirect` | `0x84cad928` | `0x84d67e00` | 5 | 4 | `f_808833e0` (834) |
| `nvnCommandBufferDrawElementsBaseVertex` | `0x84cad920` | `0x84d67d58` | 5 | 4 | `f_80435bc0` (149) |
| `nvnCommandBufferSetComputeOverlapMode` | `0x84caed20` | `0x84d68498` | 5 | 2 | `f_80665980` (317) |
| `nvnCommandBufferSetPrimitiveRestart` | `0x84cae768` | `0x84d67d18` | 5 | 4 | `f_80435bc0` (149) |
| `nvnProgramBuilderSetDefaults` | `0x84cadf20` | `0x84d67270` | 5 | 5 | none ranked |
| `nvnSamplerBuilderSetMinMagFilter` | `0x84cae310` | `0x84d677b8` | 5 | 5 | none ranked |
| `nvnSamplerBuilderSetWrapMode` | `0x84cae318` | `0x84d677c0` | 5 | 5 | none ranked |
| `nvnCommandBufferBindShadingRateImage` | `0x84caea38` | `0x84d68188` | 4 | 4 | `f_80660320` (41) |
| `nvnCommandBufferSetPointSize` | `0x84cae818` | `0x84d67ea8` | 4 | 4 | `f_80084de0` (16) |
| `nvnDeviceGetTimestampInNanoseconds` | `0x84cadcb0` | `0x84d66fe8` | 4 | 4 | `f_80665980` (317) |
| `nvnQueueFenceSync` | `0x84cae998` | `0x84d680e8` | 4 | 4 | `f_805902c0` (261) |
| `nvnSamplerPoolRegisterSampler` | `0x84cae088` | `0x84d67420` | 4 | 4 | none ranked |
| `nvnVertexAttribStateSetDefaults` | `0x84cae660` | `0x84d67b30` | 4 | 2 | none ranked |
| `nvnBufferInvalidateMappedRange` | `0x84cad840` | `0x84d674b8` | 3 | 3 | none ranked |
| `nvnCommandBufferClearTexture` | `0x84cada88` | `0x84d67f38` | 3 | 3 | `f_802f35a0` (3514) |
| `nvnCommandBufferCopyBufferToTexture` | `0x84cada40` | `0x84d67f00` | 3 | 3 | none ranked |
| `nvnCommandBufferSetColorReductionEnable` | `0x84cae920` | `0x84d68060` | 3 | 3 | `f_8074b190` (2004) |
| `nvnCommandBufferSetColorReductionThresholds` | `0x84cae928` | `0x84d68068` | 3 | 3 | `f_8074b190` (2004) |
| `nvnCommandBufferSetLineWidth` | `0x84cadae0` | `0x84d67eb0` | 3 | 3 | `f_80084de0` (16) |
| `nvnCommandBufferSetShaderScratchMemory` | `0x84cae8d0` | `0x84d68000` | 3 | 3 | `f_80665980` (317) |
| `nvnDepthStencilStateSetDefaults` | `0x84cae5f8` | `0x84d67ac8` | 3 | 2 | none ranked |
| `nvnEventSignal` | `0x84caea00` | `0x84d68150` | 3 | 3 | `f_8042cd20` (1173) |
| `nvnQueueBuilderSetComputeMemorySize` | `0x84cadd20` | `0x84d67070` | 3 | 3 | none ranked |
| `nvnQueueBuilderSetDefaults` | `0x84cadd08` | `0x84d67058` | 3 | 3 | none ranked |
| `nvnQueueBuilderSetDevice` | `0x84cadd00` | `0x84d67050` | 3 | 3 | none ranked |
| `nvnQueueBuilderSetFlags` | `0x84cadd10` | `0x84d67060` | 3 | 3 | none ranked |
| `nvnSamplerBuilderSetBorderColor` | `0x84cae338` | `0x84d677e0` | 3 | 3 | none ranked |
| `nvnSamplerBuilderSetCompare` | `0x84cae330` | `0x84d677d8` | 3 | 3 | none ranked |
| `nvnSamplerBuilderSetMaxAnisotropy` | `0x84cae350` | `0x84d677f8` | 3 | 3 | none ranked |
| `nvnSyncInitialize` | `0x84cae980` | `0x84d680d0` | 3 | 3 | none ranked |
| `nvnTextureBuilderGetStorageClass` | `0x84cae150` | `0x84d67598` | 3 | 1 | none ranked |
| `nvnTextureFlushTexels` | `0x84cae2c0` | `0x84d67768` | 3 | 3 | none ranked |
| `nvnTextureGetDepth` | `0x84cada80` | `0x84d67700` | 3 | 3 | `f_802f35a0` (3514) |
| `nvnTextureWriteTexels` | `0x84cae2a0` | `0x84d67748` | 3 | 3 | none ranked |
| `nvnVertexAttribStateSetFormat` | `0x84cae668` | `0x84d67b38` | 3 | 2 | none ranked |
| `nvnVertexAttribStateSetStreamIndex` | `0x84cae670` | `0x84d67b40` | 3 | 2 | none ranked |
| `nvnChannelMaskStateSetDefaults` | `0x84cae4f0` | `0x84d67998` | 2 | 2 | none ranked |
| `nvnCommandBufferBindMultisampleState` | `0x84cad950` | `0x84d67c38` | 2 | 2 | `f_812bd8e0` (2043) |
| `nvnCommandBufferBindTextures` | `0x84cae740` | `0x84d67cd8` | 2 | 2 | `f_81503720` (1776) |
| `nvnCommandBufferCopyTextureToBuffer` | `0x84cada48` | `0x84d67f08` | 2 | 2 | none ranked |
| `nvnCommandBufferDrawElementsIndirect` | `0x84cad938` | `0x84d67d78` | 2 | 2 | none ranked |
| `nvnCommandBufferSetConservativeRasterEnable` | `0x84cad968` | `0x84d67ee0` | 2 | 2 | `f_812bd8e0` (2043) |
| `nvnCommandBufferSetSamplerPool` | `0x84cadac0` | `0x84d67ff8` | 2 | 2 | `f_80665980` (317) |
| `nvnCommandBufferSetShadingRatePaletteMode` | `0x84caea40` | `0x84d68190` | 2 | 2 | none ranked |
| `nvnCommandBufferSetTexturePool` | `0x84cadac8` | `0x84d67ff0` | 2 | 2 | `f_80665980` (317) |
| `nvnDeviceFinalizeCommandHandle` | `0x84cad8e0` | `0x84d66ff0` | 2 | 2 | none ranked |
| `nvnDeviceGetTexelFetchHandle` | `0x84cadb58` | `0x84d66f58` | 2 | 1 | none ranked |
| `nvnDeviceRegisterFastClearColor` | `0x84cadc50` | `0x84d66f88` | 2 | 2 | none ranked |
| `nvnEventBuilderSetDefaults` | `0x84cae9c0` | `0x84d68110` | 2 | 2 | none ranked |
| `nvnEventBuilderSetStorage` | `0x84cae9c8` | `0x84d68118` | 2 | 2 | none ranked |
| `nvnEventInitialize` | `0x84cae9e8` | `0x84d68138` | 2 | 2 | none ranked |
| `nvnMemoryPoolMapVirtual` | `0x84cae018` | `0x84d673a8` | 2 | 2 | `f_8005ed00` (54) |
| `nvnMultisampleStateSetDefaults` | `0x84cae500` | `0x84d679b0` | 2 | 2 | none ranked |
| `nvnPolygonStateSetDefaults` | `0x84cae5d0` | `0x84d67a80` | 2 | 2 | none ranked |
| `nvnProgramInitialize` | `0x84cadfa8` | `0x84d672f8` | 2 | 2 | none ranked |
| `nvnProgramSetShaders` | `0x84cadfc0` | `0x84d67310` | 2 | 2 | none ranked |
| `nvnQueueBuilderSetCommandMemorySize` | `0x84cadd18` | `0x84d67068` | 2 | 2 | none ranked |
| `nvnQueueBuilderSetControlMemorySize` | `0x84cadd28` | `0x84d67078` | 2 | 2 | none ranked |
| `nvnQueueBuilderSetPrimaryQueue` | `0x84cadda8` | `0x84d670f8` | 2 | 2 | none ranked |
| `nvnQueueInitialize` | `0x84caddb8` | `0x84d67108` | 2 | 2 | none ranked |
| `nvnQueuePresentTexture` | `0x84cadde8` | `0x84d67138` | 2 | 2 | `f_80547f20` (3589) |
| `nvnSamplerBuilderSetLodBias` | `0x84cae328` | `0x84d677d0` | 2 | 2 | none ranked |
| `nvnSamplerBuilderSetLodClamp` | `0x84cae320` | `0x84d677c8` | 2 | 2 | none ranked |
| `nvnSamplerBuilderSetReductionFilter` | `0x84cae358` | `0x84d67800` | 2 | 2 | none ranked |
| `nvnTextureBuilderGetZCullStorageSize` | `0x84cae1d8` | `0x84d67620` | 2 | 2 | `f_80264bb0` (1365) |
| `nvnTextureBuilderSetPackagedTextureLayout` | `0x84cae138` | `0x84d67580` | 2 | 2 | none ranked |
| `nvnTextureGetLevels` | `0x84cae268` | `0x84d67708` | 2 | 2 | none ranked |
| `nvnTextureGetSamples` | `0x84cae270` | `0x84d67718` | 2 | 2 | none ranked |
| `nvnTextureGetStride` | `0x84cae288` | `0x84d67730` | 2 | 2 | none ranked |
| `nvnTextureGetSwizzle` | `0x84cae278` | `0x84d67720` | 2 | 2 | none ranked |
| `nvnTextureReadTexels` | `0x84cae2b0` | `0x84d67758` | 2 | 2 | none ranked |
| `nvnVertexStreamStateSetDefaults` | `0x84cae688` | `0x84d67b58` | 2 | 2 | none ranked |
| `nvnVertexStreamStateSetDivisor` | `0x84cae698` | `0x84d67b68` | 2 | 2 | none ranked |
| `nvnVertexStreamStateSetStride` | `0x84cae690` | `0x84d67b60` | 2 | 2 | none ranked |
| `nvnWindowAcquireTexture` | `0x84cadea8` | `0x84d671f8` | 2 | 2 | `f_81207a50` (3470) |
| `nvnWindowBuilderSetDefaults` | `0x84caddf8` | `0x84d67148` | 2 | 2 | `f_80547f20` (3589) |
| `nvnWindowBuilderSetDevice` | `0x84caddf0` | `0x84d67140` | 2 | 2 | `f_80547f20` (3589) |
| `nvnWindowBuilderSetNativeWindow` | `0x84cade00` | `0x84d67150` | 2 | 2 | `f_80547f20` (3589) |
| `nvnWindowBuilderSetNumActiveTextures` | `0x84cade20` | `0x84d67170` | 2 | 2 | `f_80547f20` (3589) |
| `nvnWindowBuilderSetPresentInterval` | `0x84cade10` | `0x84d67160` | 2 | 2 | `f_80547f20` (3589) |
| `nvnWindowBuilderSetTextures` | `0x84cade08` | `0x84d67158` | 2 | 2 | `f_80547f20` (3589) |
| `nvnWindowInitialize` | `0x84cade90` | `0x84d671e0` | 2 | 2 | `f_80547f20` (3589) |
| `nvnWindowSetCrop` | `0x84caded8` | `0x84d67228` | 2 | 2 | `f_81207a50` (3470) |
| `nvnWindowSetNumActiveTextures` | `0x84cadef8` | `0x84d67248` | 2 | 2 | `f_80547f20` (3589) |
| `nvnBufferGetMemoryOffset` | `0x84cad858` | `0x84d674c8` | 1 | 1 | none ranked |
| `nvnBufferSetDebugLabel` | `0x84cad848` | `0x84d67490` | 1 | 1 | none ranked |
| `nvnCommandBufferBindImages` | `0x84cae748` | `0x84d67cf0` | 1 | 1 | `f_81503720` (1776) |
| `nvnCommandBufferBindStorageBuffers` | `0x84cae730` | `0x84d67cc0` | 1 | 1 | `f_81503720` (1776) |
| `nvnCommandBufferDrawArraysInstanced` | `0x84cad918` | `0x84d67d60` | 1 | 1 | none ranked |
| `nvnCommandBufferPushGraphicsConstants` | `0x84caed10` | `0x84d68488` | 1 | 1 | none ranked |
| `nvnCommandBufferSetComputeWorkloadPriority` | `0x84caedf8` | `0x84d68570` | 1 | 1 | `f_80665980` (317) |
| `nvnCommandBufferSetConservativeRasterMode` | `0x84cae830` | `0x84d67ef0` | 1 | 1 | none ranked |
| `nvnCommandBufferSetCopyImageStride` | `0x84cada38` | `0x84d68020` | 1 | 1 | none ranked |
| `nvnCommandBufferSetCopyRowStride` | `0x84cada30` | `0x84d68018` | 1 | 1 | none ranked |
| `nvnCommandBufferSetDepthBounds` | `0x84cad9a8` | `0x84d67e38` | 1 | 1 | `f_80105ec0` (1221) |
| `nvnCommandBufferSetDepthRanges` | `0x84cad9f8` | `0x84d67e40` | 1 | 1 | none ranked |
| `nvnCommandBufferSetRasterizerDiscard` | `0x84cad960` | `0x84d67ed0` | 1 | 1 | `f_812bd8e0` (2043) |
| `nvnCommandBufferSetSampleMask` | `0x84cad958` | `0x84d67ec8` | 1 | 1 | `f_812bd8e0` (2043) |
| `nvnCommandBufferSetScissors` | `0x84cad9f0` | `0x84d67e28` | 1 | 1 | `f_812bdf20` (2925) |
| `nvnCommandBufferSetStencilCullCriteria` | `0x84caea28` | `0x84d68178` | 1 | 1 | none ranked |
| `nvnCommandBufferSetViewports` | `0x84cad9e8` | `0x84d67e10` | 1 | 1 | none ranked |
| `nvnDeviceBuilderSetDefaults` | `0x84cadbe8` | `0x84d66f00` | 1 | 1 | none ranked |
| `nvnDeviceBuilderSetFlags` | `0x84cadbf0` | `0x84d66f08` | 1 | 1 | none ranked |
| `nvnDeviceInstallDebugCallback` | `0x84cadc30` | `0x84d66f68` | 1 | 1 | none ranked |
| `nvnDeviceSetDepthMode` | `0x84cadc48` | `0x84d66f80` | 1 | 1 | none ranked |
| `nvnDeviceSetWindowOriginMode` | `0x84cadc40` | `0x84d66f78` | 1 | 1 | none ranked |
| `nvnEventFinalize` | `0x84cae9f0` | `0x84d68140` | 1 | 1 | none ranked |
| `nvnEventGetValue` | `0x84cae9f8` | `0x84d68148` | 1 | 1 | `f_805902c0` (261) |
| `nvnMemoryPoolGetSize` | `0x84cae020` | `0x84d673b0` | 1 | 1 | none ranked |
| `nvnMemoryPoolSetDebugLabel` | `0x84cadbe0` | `0x84d67378` | 1 | 1 | none ranked |
| `nvnMultisampleStateSetAlphaToCoverageEnable` | `0x84cae518` | `0x84d679c8` | 1 | 1 | none ranked |
| `nvnMultisampleStateSetMultisampleEnable` | `0x84cae508` | `0x84d679b8` | 1 | 1 | none ranked |
| `nvnMultisampleStateSetSamples` | `0x84cae510` | `0x84d679c0` | 1 | 1 | none ranked |
| `nvnProgramSetShadersExt` | `0x84cadfc8` | `0x84d67318` | 1 | 1 | none ranked |
| `nvnQueueBuilderGetQueueMemorySize` | `0x84cadd38` | `0x84d67088` | 1 | 1 | none ranked |
| `nvnQueueBuilderSetQueueMemory` | `0x84cadd40` | `0x84d67090` | 1 | 1 | none ranked |
| `nvnQueueWaitSync` | `0x84cae9a8` | `0x84d680f8` | 1 | 1 | `f_805902c0` (261) |
| `nvnSamplerGetBorderColor` | `0x84cae408` | `0x84d678b0` | 1 | 1 | none ranked |
| `nvnSamplerGetCompare` | `0x84cae400` | `0x84d678a8` | 1 | 1 | none ranked |
| `nvnSamplerGetLodBias` | `0x84cae3f8` | `0x84d678a0` | 1 | 1 | none ranked |
| `nvnSamplerGetLodClamp` | `0x84cae3f0` | `0x84d67898` | 1 | 1 | none ranked |
| `nvnSamplerGetMaxAnisotropy` | `0x84cae420` | `0x84d678c8` | 1 | 1 | none ranked |
| `nvnSamplerGetMinMagFilter` | `0x84cae3e0` | `0x84d67888` | 1 | 1 | none ranked |
| `nvnSamplerGetReductionFilter` | `0x84cae428` | `0x84d678d0` | 1 | 1 | none ranked |
| `nvnSamplerGetWrapMode` | `0x84cae3e8` | `0x84d67890` | 1 | 1 | none ranked |
| `nvnSamplerPoolInitialize` | `0x84cae070` | `0x84d67408` | 1 | 1 | none ranked |
| `nvnSyncFinalize` | `0x84cae988` | `0x84d680d8` | 1 | 1 | none ranked |
| `nvnTextureBuilderGetFlags` | `0x84cae160` | `0x84d675a8` | 1 | 1 | none ranked |
| `nvnTextureBuilderSetPackagedTextureData` | `0x84cae130` | `0x84d67578` | 1 | 1 | none ranked |
| `nvnTextureGetMemoryOffset` | `0x84cae2d8` | `0x84d67780` | 1 | 1 | none ranked |
| `nvnTextureGetMemoryPool` | `0x84cae2d0` | `0x84d67778` | 1 | 1 | none ranked |
| `nvnTextureGetTextureAddress` | `0x84cae290` | `0x84d67738` | 1 | 1 | `f_80271be0` (2205) |
| `nvnTextureInvalidateTexels` | `0x84cae2c8` | `0x84d67770` | 1 | 1 | none ranked |
| `nvnTexturePoolInitialize` | `0x84cae028` | `0x84d673c0` | 1 | 1 | none ranked |
| `nvnTextureViewGetLayers` | `0x84cada68` | `0x84d67680` | 1 | 1 | `f_802f35a0` (3514) |
| `nvnTextureViewGetLevels` | `0x84cada58` | `0x84d67678` | 1 | 1 | `f_802f35a0` (3514) |
| `nvnTextureViewGetTarget` | `0x84cada60` | `0x84d676a0` | 1 | 1 | `f_802f35a0` (3514) |
| `nvnTextureViewSetDepthStencilMode` | `0x84cae210` | `0x84d67668` | 1 | 1 | none ranked |
| `nvnWindowFinalize` | `0x84cade98` | `0x84d671e8` | 1 | 1 | `f_80547f20` (3589) |
| `nvnWindowSetPresentInterval` | `0x84cadec0` | `0x84d67210` | 1 | 1 | none ranked |

The 450 never referenced: all 57 `nvnDlss*` and all 13 `nvnDirtyTileMap*`, and by object command buffer 113, device 50,
texture builder 22, multisample state 22, program builder 19, sampler builder 15, queue builder 14, window builder 14,
window 12, texture 12, blend state 11, and fewer of the rest. They are still asked for at start-up, and the host hands
each one an address (its handler, or `unimplemented` for the ones it has none for).

## Open questions

- How many NVN calls a frame the game makes in play, by function, and how much of the recording threads' time the
  handlers take: a count in the host (`HOS_HOSTCALL_STATS`) on a Kokiri Forest run would settle the ranking here.
- `nvnCommandBufferReportValue` (12 places, no handler): what it writes and whether the clears and conditions near it
  (`f_80265760`, `f_8025e280`, `f_8025e4d0`) need it.
- Whether recording only records (no replay on the submitting thread, `RX_VK_ASYNC=1`) or a cheaper `EndRecording` /
  `CallCommands` (no copy) would move the frame rate, given that `f_80084de0` is rank 16.
- What `f_80084de0` really is: [jobs.md](jobs.md) calls it a ModuleSystem job; it records and draws mesh-shaded work.
- What the two SDK globals `f_855f6f80` compares mean, and what the command buffer's word at `+0x18` is on the console.
