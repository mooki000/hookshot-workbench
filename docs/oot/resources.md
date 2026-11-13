# Resources: files, archives and decompression

How the game finds, reads and unpacks its files: the RomFS mount and the file device over it, the resource system
(`ares::System`, with sead's `ResourceMgr` and `FileDeviceMgr` beneath it), the packs and archives it loads, and the
Zstandard (zstd) decoder that unpacks every `.zs` file, chunk by chunk, on a decompression thread while the loading
thread reads the next chunk. Looked at in November 2026 by static analysis of the binary only (nothing here was run),
to give the zstd decoder tree that `scripts/hotlist.py` lists its rows, and to say how loading is threaded and how the
rest of the game waits on it.

**LS code:** `host/nn_fs.c` (the `nn::fs` mounts and reads), `host/mods.c`
(RomFS files replaced by mods), `host/prompts.c` (`.zs` files written as raw zstd frames),
`host/hos_main.c` (`--romfs`), `scripts/hotlist.py` (the decoder tree
built at the hot flags). On `main`, `host/hos_zstd.c` (#2738, `HOS_NATIVE_ZSTD`) replaces the stream decode with libzstd.
**See also:** [maps.md](maps.md) (how a map's files are named and asked for), [textures.md](textures.md) (`.bntx` and the
terrain's resources), [jobs.md](jobs.md) (sead threads, the job record), [game-data.md](game-data.md) (a `.byml.zs` read
with the dictionary pack), [master-quest.md](master-quest.md) (the mounts the game imports)

## In short

1. **One mount for the game's files.** At start the game makes sead's `FileDeviceMgr`, which mounts the RomFS as
   `content` (`nn::fs::MountRom`, after `QueryMountRomCacheSize`). Every resource is read through a sead file device over
   that mount (`nn::fs::OpenFile`, `ReadFile` with a `ReadOption`, at the handle's position). The only other read-only
   mount is the network library's: it mounts the RomFS again as `_npln` to read `_npln:/npln_config.json` (`f_82136380`,
   `f_821376a0`). Save data, cache and temporary storage are the written mounts ([save-data.md](save-data.md)). **read**
2. **The resource system is `ares::System`.** It is set up with a control part ("Resource Control"), its memory
   ("Resource Memory"), a set of loading threads ("ARes Loading %@"), a set of compose threads ("ARes Compose %@") and a
   decompression system ("DecompSystem"). Its `calc` runs once a frame as a job named "ares::System::calc". Code asks for
   a file by path and type and gets a handle; it waits on the handle (two light events in the resource's record), or,
   from one of the system's own threads, takes another path. **read** (the parts, the wait), **guess** (which thread does
   what)
3. **Files are compressed with zstd or Yaz0, by extension.** The path the game asks for has no compression suffix; the
   system adds `.zs` (zstd, kind 2) or `.szs` (Yaz0, kind 1). For each decompression slot it makes one `szs` and one `zs`
   decompressor (sead `Decompressor`s, chosen by that extension) and one "ParallelDecompThread". **read**
4. **Dictionaries.** zstd frames name a dictionary by ID. The dictionaries come from `Pack/ZsDic.pack` (`.zs`, the
   resource "ZsDic"): each file in it becomes a zstd DDict, registered in every `zs` decompressor under its ID. A frame
   with dictionary ID 0 uses none. **read**
5. **A `.zs` file is read and decoded at once.** The `zs` decompressor reads the first chunk, parses the frame header for
   the content size and dictionary, and allocates the output. A file shorter than one chunk is decoded in one call on
   the loading thread. A longer one is streamed: the loading thread reads chunk N+1 into one half of a double buffer while
   the ParallelDecompThread decodes chunk N from the other, and waits for each chunk to finish before reusing its half.
   **read**
6. **The decoder is the game's own copy of zstd**, an older one (about 1.3.6 to 1.4.4, from what it contains: the
   `execSequenceLast7` end copy, the cold-dictionary prefetch, 8 bytes of over-copy room). It is lifted like the rest of
   the game: in LS it is plain translated code, and a profile seldom samples it because it runs while areas load
   (hence its explicit place in `scripts/hotlist.py`). **read** (the version range is a **guess**)

## Functions

### Mounting and reading files

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_809db6a0` | `SeadSystemManagers_Init` | Makes the heaps "sead::SystemManagers", "sead::ResourceMgr" and "sead::FileDeviceMgr" and the managers in them (the file device manager through `f_80c49770`), then `nn::fs::SetAllocator(f_8348d800, f_8348d820)` | read |
| `f_80c499d0` | `FileDeviceMgr_MountContent` | Reached from `f_80c49770` → `f_80c497f0`: `nn::fs::QueryMountRomCacheSize`, allocates that much (8-aligned) from the heap it is given, stores it at `+0x28`, and calls `nn::fs::MountRom("content", cache, size)` | read |
| `f_8006bb40` | `FileDevice_Open` | Slot 9 of the file device vtable `0x84c16100`: `nn::fs::GetEntryType`, `OpenFile` and, for a file opened to write, `SetFileSize`; closes the file on failure | read |
| `f_8348a140` | `FileDevice_Read` | Slot 14: `nn::fs::ReadFile` (with a `ReadOption`) at the handle's position (`+0x20`) for the handle's file (`+0x18`); adds what was read to the position, stores the result at the device's `+0x5c` and answers whether it was 0 | read |
| `f_80ba1b90` | `FileDevice_GetFileSize` | Slot 19: `nn::fs::GetFileSize` of an open handle | read |
| `f_80459cc0` | `SarcArchive_Prepare` | Prepares a SARC archive over its bytes: checks the magic "SARC", reads the byte-order mark (0xfffe) to pick the endian reader, the version (0x100), then the "SFAT" and "SFNT" sections. Called by `f_8045a1bc`, `f_8069ce14`, `f_807978c0`, `f_80f4dd30` and through the vtables at `0x84c2e498` and `0x84c43798` | read |

### The resource system (`ares::System`)

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_836d8ad0` | `AresSystem_Init` | Sets up `ares::System` from a settings block (about 3,000 instructions): "Resource Control", "Resource Memory", the "LoadingThreads" (each named "ARes Loading %@"), the "ComposeThreads" ("ARes Compose %@"), the "DecompSystem" heap (the setting at `+0x20`, 2 MB when 0) with one decompression slot per thread (the settings at `+0xc` plus `+0x14`, capped by `+0x18` unless it is -1), each made by `f_8069a5e0`, and the "zsdic" heap (the setting at `+0xc0`) | read |
| `f_801bcd40` | `AresSystem_Calc` | Slot of the vtable at `0x84c88818`: from `ares::System` (`[0x84ea2fe0]`) queues a job record labelled "ares::System::calc" (`f_801bca90`), then, when the object at `[0x84e9f7c8]` has work pending, try-locks its two mutexes and runs `f_801c07e0` | read |
| `f_83213890` | `ResourceSettings_Init` | A vtable slot (`0x84bb9048`) of the framework's set-up that fills the resource settings: the size table path `System/Resource/ResourceSizeTable.%s.%s.Nin_Ounce_NVN2.rsizetable` (with "Product"), `Pack/ResidentCommon.pack` (with 0x100000), `Pack/ZsDic.pack`, `Pack/GeneralFormat/Global.%s.gfpack.byml` ("Product" or "All") | read |
| `f_83bb6eb0` | `ResourceModule_Init` | The "ResourceModule" set-up: the heaps "ResourceU", "ResourceS", "ResourceL", "ResourceG", "ResourceDebug", `Local/IgnorePackContent.txt`; it loads the dictionaries either one file at a time (`%s/%s`, `f_836dd060`) or from the pack (`f_811fdd00`) | guess |
| `f_80b13af0` | `ResourceHeaps_SetFileTypes` | Lists file extensions by heap: pack, zsdic, sarc, genvb, bmodelsh, bfres, bfsha, bgyml, bcett.byml, asb, ainb, bphsh, bphhf, bpheb, bphnm, bphnv, bphsc, baev, and the heaps "ForResourceV", "ForResourceU", "ForResourceS", "ForResourceL"; a vtable slot (`0x84bbe0b8`, `0x84c879a8`) | guess |
| `f_80383e80` | `Resource_LoadForSync` | Makes a request labelled "Load(ForSync)", makes the calls `f_8002f220` ([maps.md](maps.md)) also makes (`f_836dc5b0`, `f_801b3f60`, `f_801b5590`, `f_801b4090`) and waits with `f_8080c610`. Four callers (`f_801b43bc`, `f_803837ac`, `f_803845b4`, `f_80385084`) | guess |
| `f_801b5590` | `Resource_Submit` | Hands a request to the system under its mutexes, clearing and waiting on light events as it goes; called by `f_8002f220`, `f_80383e80` and 9 others | guess |
| `f_8080c610` | `ResourceHandle_WaitReady` | Waits for a requested resource (75 callers): issues the request first if its state is 1 (`f_801b27a0`), then waits on the record's light event at `+0x140`; then, if the calling thread's context (TLS) lies between the system's `+0x98` and `+0xa0`, runs `f_80036920` on the record's `+0x158`, otherwise waits on a second light event at `+0x1c8`. Answers 1 | read |
| `f_801b4090` | `ResourceHandle_Release` | Lets a handle go (159 callers): marks it, drops the reference count at `+0x94` of the resource's record (the records are 0x398 bytes, in `ares::System`'s `+0x440`, `+0x438` of them), and when it reaches 0 pushes the record's index on a lock-free ring (`+0x4d0` to `+0x4e8`) for unloading | read |
| `f_836d80e0` | `Path_Extension` | The text after a path's last "." (the empty string when there is none) | read |
| `f_836d8180` | `Path_AddCompressionExt` | Appends ".szs" (kind 1) or ".zs" (kind 2) to a path string, within its capacity | read |
| `f_83bb3eb0` | `GeneralFormatAsync_Init` | Creates the resource system's `GeneralFormatAsync_` work pool: priority-24 workers, one per selected core among 0 to 5. What its jobs do to a resource is not worked out | read |

`f_8002f220` (`Resource_Request`, [maps.md](maps.md)) is the request call most loaders use; `f_80036c20` turns a layout's
work path into its RomFS path. The terrain's resources have their own manager and prefetch hook
(`TeraFileResourceMgr_Init`, `f_80f6cf50`, and `f_80387f40` in [textures.md](textures.md)); the terrain manager names a
"resource sharc deploy work" heap.

### Decompressors and their threads

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8069a5e0` | `DecompSlot_Create` | Makes one decompression slot (0x28 bytes, layout below): a work buffer (`f_80f6d4e0`), the "ParallelDecompThread" (0xd8 bytes, `f_8069dbe0`: a sead thread with a 32-message light queue, 16 KB stack, priority 19, vtable `0x84c376e0`) pinned to the core mask it is given (`f_80ed84a0`: `nn::os::SetThreadCoreMask`) and started, an "szs" decompressor (0x150 bytes, `f_8069a340`) and a "zs" decompressor (0x690 bytes, `f_8069a490`, vtable `0x84c377e8`); the chunk size is half the size it is given, rounded up to 32. `AresSystem_Init` gives the slots the core masks 1<<5, 1<<4, … 1<<0, then 1<<5 again | read |
| `f_8069a8d0` | `ParallelDecompJob_Construct` | Makes the job inside a `zs` decompressor (its `+0x5a0`): vtable `0x84c377c0`, an empty name, two event objects (`+0x48`, `+0x78`), the fields from `+0xa8` cleared, and signals the "finished" event (`+0x68`) so that the first stream need not wait | read |
| `f_801c5e80` | `DecompQueue_Post` | The `zs` decompressor's queue object (`+0x668`, vtable `0x84c37578`): posts the job to its thread (`+0x670`) through the thread's slot 2, `f_801c5ea0` (`SendLightMessageQueue`) | read |
| `f_836dda20` | `ParallelDecompThread_Run` | The thread's slot 14 (`+0x70`), which `SeadThread_Run` (`f_80df2680`, [jobs.md](jobs.md)) calls for each message: calls the message's slot 2, which for the job is `f_80115bf0` | read |
| `f_80108da0` | `ZsDecompressor_TryDecompFromDevice` | Slot 2 of the `zs` decompressor: loads and decodes a file (args: load arg, resource, out size, out allocated size, out allocated). Opens the file through its device, makes the work memory if it has none (0x471f9 + 2 chunks + 0x1e bytes: the context, 128 KB of staging, the read buffer), reads the first chunk, reads the frame header twice (`f_80109790`) for the content size and the dictionary ID, finds the DDict (`f_834ac540`) when the ID is not 0, allocates the output (content size rounded up to 32) unless the caller gave one. A first read shorter than a chunk is decoded at once with `f_8010b380`. Otherwise it waits for the job's "finished" event (`+0x608`), sets up the job's stream (state 1) and loops: post the job for chunk N, read chunk N+1 into the other half, wait for the job's "chunk done" event (`+0x638`); it stops at a short read or an error (job `+0xbc` negative). Done when the stream's state is 4 | read |
| `f_834ac540` | `ZsDecompressor_FindDictionary` | The DDict for a dictionary ID, from the tree at the decompressor's `+0x70` (node `+0x18` the ID, `+0x20` the DDict). The search has no end test: an ID that was never registered walks into a null node | read |
| `f_834ac440` | `ZsDecompressor_AddDictionary` | Registers a DDict in the tree at `+0x70` under its ID (`f_834ab970`) | read |
| `f_811fdd00` | `ZsDic_LoadPack` | Loads the resource "ZsDic" (with 0x140000 bytes), and for each file in it makes the dictionary resource (`f_80c76b10`, `f_836dd120`) and registers its DDict in every slot's `zs` decompressor (the system's `+0x150` slots at `+0x158`, the decompressor at slot `+0x10`) | read |
| `f_836dd060` | `ZsDic_LoadOne` | The same for one dictionary file by path, through the loader at `[0x84e9f5c0]` | read |
| `f_83bb15b0` | `ZsDicResource_CreateDDict` | A vtable slot (`0x84c88168`) of the dictionary resource: makes its DDict over the file's bytes with `f_834ab740` | guess |

### Model material blocks (the OptimizeChangeBank thread)

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_804566e0` | `ModelResource_BuildMatBlock` | "gsys::ModelResource(MatBlock)", on the OptimizeChangeBank thread: rebuilds a model's material block from heaps its caller names (its second argument's `+8` for the CPU's parts, `+16` for the GPU's), small per-model heaps that are children of a large one. None of its four allocations is checked: two tryAllocs of CPU buffers (stored at the block's `+200`, its bump allocator's memory, and `+168`) and two GPU allocations through `f_80522050`. A full heap left a null pointer and a crash a few frames later (issue 1697). The four are hooked by `patch_heap_retry`, which tries a full heap's parents up to the root (`host/oot_heap_retry.c`) | read |
| `f_80522050` | `GpuMemory_Alloc` | The GPU memory a model's material block is made with (called at 0x80456f18 and 0x80457094, and from some 30 other places, which stay as shipped); a failure leaves the block with no memory pool | read |
| `f_8044cb30` | `MatBlock_BumpStore` | The first store through a material block's bump allocator: with a null buffer it writes guest 0x8, issue 1697's crash | read |
| `f_8093f940` | `MatBlock_Destroy` | The material block's destructor: frees `+168` and `+200` through `f_8021dea0` | read |
| `f_8021dea0` | `Heap_FreeByAddress` | The game's free by address: walks the heap tree for the heap whose range holds the block, so a block taken from a parent goes back to that parent | read |

### The zstd decoder: the stream and the frame

The tree `scripts/hotlist.py` lists, from the job down. Names in the `ZSTD_`, `HUF_`, `FSE_` and `XXH64_` forms are the
zstd library's own functions that each address matches; error results are zstd's negative codes (-20 corruption, -22
checksum wrong, -32 dictionary wrong, -44 table log too large, -70 destination too small, -72 source size wrong).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80115bf0` | `ParallelDecompJob_Run(job)` | Slot 2 of the job vtable `0x84c377c0`, run on the ParallelDecompThread for one chunk. Does nothing once an error is stored (`+0xbc` negative). Otherwise decodes the chunk with `f_80115c80(job+0x10, buffer, length, ddict)`: the buffer is `+0xa8`, or `+0xa8` plus the half size `+0xb0` when the chunk count `+0xb4` is odd, the length `+0xb8`, the DDict `+0xc0`. Counts the chunk; on 0 (the frame is out) signals "finished" (`+0x68`), on an error stores it at `+0xbc` and signals "finished"; then always signals "chunk done" (`+0x98`) | read |
| `f_80115c80` | `ZsStream_DecodeChunk(S, src, size, ddict)` | The game's stream wrapper around zstd's buffer-less API (layout of `S` below). State 1: reads the frame header from the chunk (`f_80109790`; an error or an unknown content size fails with -1), stores the content size as the bytes still to come (`+0x14`), sets up the context in the workspace (`+0x18`, which must be 8-aligned) as `decompressBegin` does, then `f_834ab610` when there is a DDict, and moves to state 2. State 2: walks the chunk by zstd's "expected" size (context `+0x7068`), running `decompressContinue` inlined: the frame header, block headers, raw blocks (memcpy), RLE blocks (memset), compressed blocks (`f_80116490`), skippable frames, the checksum (`XXH64_update`/`XXH64_digest`, `f_834ac110`/`f_834ac2a0`); a piece split between chunks is gathered in the staging buffer (`+0x20`, fill `+0x2c`) and decoded with `f_80113880`. Advances the output (`+8`) and room (`+0x10`). Answers the bytes still to come, 0 when done (state 4), -1 on an error, -2 when the room ran out (`ZSTD_getErrorCode` 70), -3 for input after the frame (state 3 on any failure). On `main`, `patch_zstd_stream` (#2738) puts `hos_zstd_stream` first in it | read |
| `f_80109790` | `ZSTD_getFrameHeader_advanced(zfh, src, size, format)` | Parses a frame header: magic 0xFD2FB528 (or the skippable range 0x184D2A5x), the descriptor byte, window, dictionary ID and content size fields (sizes from `0x83e79f40` and `0x83e7a7a0`). Answers 0, the size it still needs, or an error. Also called by `f_80108da0`, `f_80f70070`, `f_80525a60` | read |
| `f_80113880` | `ZSTD_decompressContinue(dctx, dst, cap, src, size)` | The full, out-of-line `decompressContinue` (about 1,900 instructions), used for the staged pieces: checks the size against "expected", the continuity of `dst`, then the stage machine (context `+0x70a4`: 0 frame header size, 1 frame header, 2 block header, 3 block, 4 last block, 5 checksum, 6 skippable header, 7 skip) with the whole block decoder inlined (literals, sequence headers, the sequence loop) | read |
| `f_834ab610` | `ZSTD_decompressBegin_usingDDict(dctx, ddict)` | Resets the context (expected 5, or 1 without magic; repeat offsets 1, 4, 8; the table pointers to the context's own tables; the Huffman table header 0x0c00000c) and, with a DDict, marks whether its content is cold (not the last one used), takes its dictionary ID, makes its content the prefix, and points the four tables at the DDict's when it has entropy tables, with its repeat offsets | read |
| `f_834ac0b0` | `XXH64_reset(state, seed)` | Starts the frame's checksum (the context's `+0x70b0`) | read |
| `f_834ac110` | `XXH64_update(state, input, len)` | Adds decoded bytes to the checksum (32-byte stripes, the rest kept in the state) | read |
| `f_834ac2a0` | `XXH64_digest(state)` | The checksum, compared with the frame's last 4 bytes | read |
| `f_8572f040` | `memmove` | The SDK's `memmove` (`0x84cdf3c0` is its import slot), used by `f_834ab4a0` | read |

`f_80d5ba00` (`ZSTD_getErrorCode`) turns a result into zstd's positive error code (0 for success).

### The zstd decoder: blocks, literals and sequences

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80116490` | `ZSTD_decompressBlock_internal(dctx, dst, cap, src, size)` | Decodes one compressed block (at most 128 KB). The literals section (`decodeLiteralsBlock`, inlined): raw (memcpy into the literal buffer `+0x71d8`), RLE (memset), compressed with new tables (4 streams `f_80112880`, 1 stream `f_801156e0`) or with the last tables ("treeless": `f_80110860`, `f_801099a0`, `f_834abda0` by the table's kind); prefetches a cold dictionary's Huffman table for more than 768 literals. Then the sequence headers (`f_801117a0`), the bit stream and the three FSE states (masks `0x83f45658`), the sequences (`f_801102e0`), and the last literals | read |
| `f_801117a0` | `ZSTD_decodeSeqHeaders(dctx, nbSeq, src, size)` | The number of sequences (1 to 3 bytes, 0x7f00 for the long form), the three modes byte, and for each of literal lengths, offsets and match lengths its decoding table: predefined, RLE, read from the block (`f_8010ffc0` then building the FSE table into the context's `+0x20`, `+0x1028`, `+0x1830`) or repeated (prefetching a cold dictionary's table) | read |
| `f_801102e0` | `ZSTD_decompressSequences_loop` | The sequence loop, with its state prepared by the caller: decodes each sequence (offset with the three repeat offsets, match length, literal length) from the three FSE states, copies its literals and its match (wild copies of 8 and 16 bytes, the external dictionary through the prefix pointers), and hands a sequence too near the end of the output to `f_834ab4a0`; stores the repeat offsets back | read |
| `f_834ab4a0` | `ZSTD_execSequenceLast7` | One sequence near the end of the output: checks the room (-70) and the literals' limit (-20), copies the literals bytewise past the last 8 bytes, then the match, from the dictionary's end when its offset reaches past the prefix (`memmove`, in one or two parts) | read |

### The zstd decoder: Huffman and FSE

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80112880` | `HUF_decompress4X_hufOnly_wksp` | 4-stream compressed literals with a new table: picks the decoder by the ratio of the sizes and the timing table `0x83f456d8` (`HUF_selectDecoder`), then either reads a double-symbol table (`f_8010ad60`) and decodes with `f_801099a0`, or reads a single-symbol table (inlined, with the weights through `f_8010ffc0` and `f_80113080`) and decodes with `f_80110860` | read |
| `f_801156e0` | `HUF_decompress1X1_DCtx_wksp` | 1-stream literals with a new table: reads a single-symbol table (`f_80115880`, 256 symbols, then fills it), then decodes with `f_8010b160` | read |
| `f_8010ad60` | `HUF_readDTableX2_wksp` | Reads a double-symbol (two symbols a lookup) Huffman table, table log at most 12, workspace at least 0x5dc bytes; the weights through `f_80115880`. Also used for a dictionary's table (`f_834abae0`) | read |
| `f_80115880` | `HUF_readStats` | The Huffman weights: raw 4-bit when the header byte is 128 or more, otherwise FSE-compressed (`f_8010ffc0` for the counts, table log at most 6, the table built inline, `f_80113080` to decode); the rank counts and the table log | read |
| `f_8010ffc0` | `FSE_readNCount` | Reads an FSE header's normalized counts; copies a header shorter than 8 bytes into a zeroed buffer and calls itself on it | read |
| `f_80113080` | `FSE_decompress_usingDTable` | Decodes an FSE stream with two interleaved states (the fast mode when the table says so); used for the Huffman weights | read |
| `f_80110860` | `HUF_decompress4X1_usingDTable_internal` | 4 streams (the 6-byte jump table), single-symbol table: one byte a lookup | read |
| `f_801099a0` | `HUF_decompress4X2_usingDTable_internal` | 4 streams, double-symbol table: one or two bytes a lookup | read |
| `f_8010b160` | `HUF_decompress1X1_usingDTable_internal` | 1 stream, single-symbol table, four lookups between reloads | read |
| `f_834abda0` | `HUF_decompress1X2_usingDTable_internal` | 1 stream, double-symbol table (the treeless single-stream case) | read |

### The zstd decoder: dictionaries and whole-buffer decodes

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8010b380` | `ZSTD_decompressMultiFrame(dctx, dst, cap, src, size, dict, dictSize, ddict)` | Decodes a whole buffer in one call (about 4,900 instructions: the frame, block, literals and sequence code inlined again, with its own calls into the tree above and `f_834abae0` for a raw dictionary). Called by `f_80108da0` (short files), `f_80c17020` (a vtable slot at `0x84c77ec0`) and `f_80f700d0` | read |
| `f_80f700d0` | `Zstd_DecodeBuffer` | Sets up a context in a workspace it is given and decodes a buffer with `f_8010b380`; -1 or the error code from `f_80d5ba00` on failure. Used by `f_80f70240` (which reads a file into the "HeapSizeDBWorkHeap", near "MountRomCache") and `f_80a335e0` | read |
| `f_80f70070` | `Zstd_FrameContentSize(src, size)` | A frame's content size from its header (`f_80109790`): 0 for a skippable frame, -1 unknown, -2 an error | read |
| `f_834ab740` | `ZSTD_createDDict_advanced` | Allocates a DDict (0x6860 bytes) and fills it with `f_834ab850` | read |
| `f_834ab850` | `ZSTD_initDDict_internal` | Copies the dictionary's bytes or keeps a reference to them, then, for a dictionary with the magic 0xEC30A437, takes its ID and loads its entropy tables (`f_834abae0`) | read |
| `f_834abae0` | `ZSTD_loadDEntropy` | A dictionary's tables: the Huffman table (`f_8010ad60`), then the three FSE tables' counts (`f_8010ffc0`, three times) and the repeat offsets | read |
| `f_834ab970` | `ZSTD_getDictID_fromDDict` | The ID of a DDict's dictionary (0 when it lacks the magic or is shorter than 8 bytes) | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84ea2fe0` | `g_AresSystem` | `ares::System`: read by `AresSystem_Calc` and `ResourceHandle_Release` (and 70 other places) | guess |
| `0x84e9f5c0` | `g_SeadResourceMgr` | The loader `ZsDic_LoadOne` loads through; the `zs` decompressor's destructor unregisters itself from it (`f_8349f0f0`) | guess |
| `0x84c16100` | `FileDevice_vtable` | A file device's vtable: slot 9 `f_8006bb40` (open), 10 `f_80ba1bf0` (close), 11 flush, 12 delete, 14 `f_8348a140` (read), 15 write, 19 `f_80ba1b90` (size), 22 to 25 the directory calls | read |
| `0x84c376e0` | `ParallelDecompThread_vtable` | Slot 2 `f_801c5ea0` (send a message), slot 14 `f_836dda20` (run a message) | read |
| `0x84c377c0` | `ParallelDecompJob_vtable` | Slot 2 `f_80115bf0` | read |
| `0x84c377e8` | `ZsDecompressor_vtable` | Slot 2 `f_80108da0`; slot 0 its destructor `f_836dea10` | read |
| `0x84c37770` | `SzsDecompressor_vtable` | The `szs` decompressor's; slot 2 is `f_836de2b0` | guess |
| `0x83f456d8` | `HUF_algoTime` | The Huffman decoder timing table, 16 rows of 3 (table time, time per 256 bytes) | read |
| `0x83f45658` | `BIT_mask` | The bit-stream masks (0, 1, 3, 7, …) | read |
| `0x83f4564c` | `ZSTD_repStartValue` | The repeat offsets a frame starts with: 1, 4, 8 | read |
| `0x83e79f40` | `ZSTD_did_fieldSize` | Dictionary ID field sizes: 0, 1, 2, 4 | read |
| `0x83e7a7a0` | `ZSTD_fcs_fieldSize` | Content size field sizes: 0, 2, 4, 8 | read |
| `0x83cbda26` | `ParallelDecompThreadName` | "ParallelDecompThread" | read |
| `0x83dee5ef` | `ZsDicPackPath` | `Pack/ZsDic.pack` | read |
| `0x83dd4265` | `ResidentCommonPackPath` | `Pack/ResidentCommon.pack` | read |
| `0x83de1b2b` | `ResourceSizeTablePathFormat` | `System/Resource/ResourceSizeTable.%s.%s.Nin_Ounce_NVN2.rsizetable` | read |
| `0x83d14e55` | `ZsExt` | ".zs" | read |
| `0x83cf097c` | `SzsExt` | ".szs" | read |

## Structures

### A decompression slot (0x28 bytes, `f_8069a5e0`)

| Offset | Field | Sure |
|---|---|---|
| `+0` | The work buffer | read |
| `+8` | The `szs` decompressor | read |
| `+16` | The `zs` decompressor | read |
| `+24` | The ParallelDecompThread | read |
| `+32` | Its core mask | read |
| `+36` | Its priority (19) | read |

### The `zs` decompressor (0x690 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0` | vtable `0x84c377e8` | read |
| `+0x70` | The dictionaries by ID (a tree) | read |
| `+0x590` | The chunk size (half the read buffer, a multiple of 32) | read |
| `+0x598` | The work memory: the context (0x271f8 bytes) at its start, 128 KB of staging after it, the read buffer (32-aligned) after that | read |
| `+0x5a0` | The ParallelDecomp job (below) | read |
| `+0x668` | The queue object (vtable `0x84c37578`) | read |
| `+0x670` | The ParallelDecompThread | read |

### The ParallelDecomp job (at the `zs` decompressor's `+0x5a0`)

| Offset | Field | Sure |
|---|---|---|
| `+0` | vtable `0x84c377c0` | read |
| `+16` | The stream `S` (below) | read |
| `+0x68` | "Finished" light event: the stream is done or failed (signalled when made) | read |
| `+0x98` | "Chunk done" light event | read |
| `+0xa8` | The read buffer (two halves) | read |
| `+0xb0` | The half size | read |
| `+0xb4` | Chunks decoded (its parity picks the half) | read |
| `+0xb8` | This chunk's length | read |
| `+0xbc` | The first error (negative), 0 before | read |
| `+0xc0` | The DDict, or 0 | read |

### The stream `S` (`f_80115c80`'s first argument)

| Offset | Field | Sure |
|---|---|---|
| `+0` | State byte: 1 start, 2 decoding, 3 failed, 4 done | read |
| `+8` | Output pointer | read |
| `+16` | Output room (signed 32-bit) | read |
| `+20` | Bytes still to come (the content size at the start) | read |
| `+24` | The workspace for the context (8-aligned) | read |
| `+32` | The staging buffer | read |
| `+40` | Its size (128 KB for the `zs` decompressor) | read |
| `+44` | Its fill | read |
| `+48` | The context, once started | read |

### The decoder context (zstd's `ZSTD_DCtx`, 0x271f8 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0` | The literal-length, match-length, offset and Huffman table pointers (8 bytes each) | read |
| `+0x20` | The literal-length FSE table (513 entries of 8 bytes) | read |
| `+0x1028` | The offset table | read |
| `+0x1830` | The match-length table | read |
| `+0x2838` | The Huffman table (a 4-byte header and 4096 entries) | read |
| `+0x683c` | The three repeat offsets | read |
| `+0x7048` | The end of the last output (continuity) | read |
| `+0x7050`, `+0x7058`, `+0x7060` | The prefix start, the virtual start, the dictionary's end | read |
| `+0x7068` | The size the next call must be given ("expected") | read |
| `+0x7070` | The frame header: content size, window, block size, frame type, header size, dictionary ID (`+0x708c`), checksum flag (`+0x7090`) | read |
| `+0x7098` | Bytes decoded so far | read |
| `+0x70a0` | The block type (0 raw, 1 RLE, 2 compressed) | read |
| `+0x70a4` | The stage | read |
| `+0x70b0` | The XXH64 state | read |
| `+0x7168` | The dictionary's ID | read |
| `+0x716c` | Whether the dictionary is cold | read |
| `+0x71d8` | The literal buffer (128 KB and 8 bytes) | read |
| `+0x271e0` | The header buffer | read |

### A DDict (0x6860 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0` | Its own copy of the dictionary, or 0 | read |
| `+8` | The content | read |
| `+16` | The size | read |
| `+0x18` | The tables: literal lengths, offsets `+0x1020`, match lengths `+0x1828`, Huffman `+0x2830` | read |
| `+0x6834` | The repeat offsets | read |
| `+0x6840` | The dictionary ID | read |
| `+0x6844` | Whether it has entropy tables | read |

## Notes

### How a `.zs` file is loaded

1. Code asks for a resource by path and type (`f_8002f220`, or `Resource_LoadForSync`). The path has no `.zs`;
   `Path_AddCompressionExt` adds it (or `.szs`), and the decompressor is the one whose extension matches.
2. On a loading thread, `ZsDecompressor_TryDecompFromDevice` opens the file through the `content` file device and reads
   one chunk. The frame header gives the content size, which sizes the output, and the dictionary ID, which picks a DDict
   registered from `Pack/ZsDic.pack`.
3. A file that fits in one chunk is decoded right there (`f_8010b380`). A longer file goes to the slot's
   ParallelDecompThread one chunk at a time, with the read of the next chunk overlapping the decode of this one. The
   loading thread blocks on the job's "chunk done" event after each read, so a file takes about the longer of its read
   and its decode.
4. Each slot has one `zs` decompressor and one thread, and a stream waits for the slot's previous stream to finish
   ("finished" event), so one slot decodes one file at a time. The slots' threads are pinned to one core each, cores 5 down to 0 and round again (`nn::os::SetThreadCoreMask`).

### How the rest of the game waits

A resource handle has two light events in its record; `ResourceHandle_WaitReady` blocks the caller on them. A caller
that is one of the system's own threads (by its thread context) takes `f_80036920` instead of the second wait, so a
loading thread that needs another resource does not block on itself. Many loaders wait like this (75 callers); the terrain has its own waits (`SeqExecuteWaitTerrainResourceStable`, [textures.md](textures.md)).

### What LS does with it

- **The RomFS** is a folder: `MountRom` (both forms) mounts whatever name the game gives, `content` here, on the folder
  given by `--romfs` or the `romfs` folder beside the image, read-only; `QueryMountRomCacheSize` answers 0x10000 and the
  cache is not used. Reads are positional, so the loading threads can read one file at once (`host/nn_fs.c`). A mod's
  file replaces the game's at the same RomFS path (`host/mods.c`).
- **`.zs` files LS writes** (the button prompts' textures, layouts and font, `host/prompts.c`) are zstd frames of
  raw 128 KB blocks with dictionary ID 0, so the game's decoder takes them without a dictionary
  (`ZsDecompressor_TryDecompFromDevice` looks a dictionary up only for a non-zero ID).
- **The decoder** is lifted code. `scripts/hotlist.py` builds the tree at the hot flags whatever the profile says
  (a whole decode takes 16% less time there). On `main`, `HOS_NATIVE_ZSTD` (#2738, off by default) has `f_80115c80`
  decode on the host's libzstd instead; its notes say the lifted decode runs for more than 250 ms during loads with no OS
  call, holding a virtual core, and its runs in the standard test found no difference outside the run-to-run range
  (`docs/tested-configurations.md` on `main`).

## Open questions

- How many loading and compose threads (and so decompression slots) the shipped settings make, their priorities, and
  the chunk size (from the size `f_8069a5e0` is given); which thread runs `ZsDecompressor_TryDecompFromDevice` (a
  loading thread is a **guess**).
- What the compose threads do (building resources after load, such as creating DDicts, textures or SARC views, is a
  **guess**).
- The path from `f_8002f220` and `f_801b5590` to the decompressor: whether `ares::System` calls sead's `ResourceMgr`
  to pick the decompressor by extension (likely, from the `zs` decompressor's registration with `[0x84e9f5c0]`), and
  what `f_836dc5b0` and `f_801b3f60` do.
- What `f_80036920` does for a waiter on one of the system's threads: run the pending work inline or wait another way.
- Whether any RomFS file is Yaz0 (`.szs`); the `szs` decompressor's `f_836de2b0` was not read.
- Whether `ResourceModule_Init` loads the dictionaries from the pack or one by one in the shipped game, and which
  dictionaries `Pack/ZsDic.pack` holds besides `bcett.byml.zsdic` ([maps.md](maps.md)).
- The game's zstd version: no version string was found; the range above comes from what the code contains. No long-offset
  (prefetching) sequence decoder was found in the stream's tree.
- What a `.zs` file with an unregistered dictionary ID does in LS: in the game's code the lookup walks into a null
  node (`ZsDecompressor_FindDictionary`).
- The `Unload(ForSync)` users (`f_801b52d0`, `f_801b5430`, `f_801b80a0`, `f_801b8730`, `f_801be6e0`) and `ResidentCommon`'s
  0x100000.
