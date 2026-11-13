# Master Quest

How the game decides it owns the Master Quest add-on, how the title and the new-game flow use the answer, what Master Quest
mode changes, and where its saves go. It is written for a future `nn::aoc` in LS: which calls the game makes, what it
does with the answers, and what it asks for once the add-on is reported. Looked at in November 2026.

**LS code:** none yet
**See also:** [game-data.md](game-data.md) (`account.sav`, `IsLastSelectedMQMode`), [save-data.md](save-data.md) (the
slots and captions), [events.md](events.md) (`f_829df380`, the change of map)

## In short

1. **At boot,** the game asks `nn::aoc` once which add-ons it owns. If index **1** is among them, it sets a byte in its
   entitlement object (`+216`). Nothing asks again while the game runs. **read**
2. **The new-game flow** turns Master Quest mode on through `SeqOneShotSetMQMode`, and only if that byte is set. Without it,
   the node also clears the saved `IsLastSelectedMQMode` bool. **read**
3. **In Master Quest mode,** each of 13 dungeons is its own map, `<Dungeon>_MQ`, with its own map-index entry and layout
   (which this dump lacks), and entries in the `AoC1/` per-map tables. Maps outside that preload a dungeon ask for its
   `_MQ` layout. No geometry, collision or navigation loader looks at the mode. The saves go to slots 6 to 11. **read**
   (see [maps.md](maps.md) for how a map loads)
4. **The ".120" resources** are asked for when the add-on is owned and the release version is at least 120. **read**
5. **LS today:** the two `nn::aoc` calls are unimplemented and return 0, so the byte stays 0 and the title never
   offers Master Quest. **tested** (the log)

## Functions

### The add-on check at boot

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80eefdb0` | `Entitlement_Create` | Makes the entitlement object (0x100 bytes, vtable `0x84bf86d0`; layout below), called from `nnMain` (`f_81222ce0`, [graphics.md](graphics.md)). `+208`, `+212` and `+216` start at 0. The holder is `0x84e97e48`, and `nnMain` copies the pointer to `0x84eb5f40` at about 0x81223140 | read |
| `f_83392a00` | `Entitlement_OnBootPhase(obj, phase)` | Slot 5 (`+40`) of the vtable; the phase is a byte in `w1`. **Phase 2 is the add-on check:** `CountAddOnContent()`; below 1 (errors count as 0) the list call is skipped; otherwise `n = min(count, 32)`, a 128-byte buffer on the stack is zeroed and `ListAddOnContent(buffer, 0, n)` is called (its result is ignored). If any of the first `n` entries is exactly **1**, it stores 1 at `+216` and stops looking. It never clears `+216`. Both paths then start "BootupBackgroundWorkThread" (`f_8338f290`). Phases 3, 10, 19, 21, 28, 43, 44 and 51 set bits in `[[0x84e96868]+48]`, which has nothing to do with add-ons | read |
| `f_8517d900` | `nn::aoc::CountAddOnContent` | The import's stub (PLT `f_83c62340`). Its only caller is `f_83392a00`, at 0x83392a38 | read |
| `f_8517d950` | `nn::aoc::ListAddOnContent` | The import's stub, `(int *out, int offset, int count)` (PLT `f_83c62350`). Its only caller is `f_83392a00`, at 0x83392a70 | read |
| `f_8338f290` | `BootupWorker_Start` | Starts "BootupBackgroundWorkThread" (`[0x84e96868]`) after the check | read |
| `f_83392c80` | `Entitlement_vt6` | Slot 6: sets `+208` to 1 or 2 and clears bit 0 of `+212`. Called through `0x84eb5f40` from `f_80706be0`. Not add-on related | read |
| `f_83392250` | `Entitlement_Destroy` | Clears `0x84e97e48` | read |

The phases come from the ModuleSystem's boot, `f_83b7a2c0` ([graphics.md](graphics.md)), through the copy at `0x84eb5f40`:
phase 0 at 0x83b7a694, 1 at 0x83b7a778, 2 at 0x83b7a858. A phase is sent only when it differs from the current one. Two
more calls send a phase held in a register: the loop at 0x83b7aac4 sends only phases 3 and up, and 0x83b7ae78 sends the
start phase (a byte from a virtual call), which phases 0 to 2 are skipped for. So phase 2 goes out exactly once. Slot 5 is
called only through `0x84eb5f40` from these places, and `f_83b7a2c0` is called only from `f_83b797e0` (slot 10 of the
vtable `0x84c85650`), which calls it once and then runs a frame loop that never returns. **read**; **guess** that
`f_83b797e0` itself runs once. So the game asks `nn::aoc` once per start.

### The release version (RegionLangMask)

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80fb1560` | `RegionLanguageMgr_Create` | Makes the region and language manager (0x68 bytes, layout below); its holder is `0x84e9f558` | read |
| `f_80fb14b0` | `RegionLanguageMgr_Load` | Loads `System/RegionLangMask.txt` and `Local/System/RegionLangConfig.txt` | read |
| `f_80fb1600` | `RegionLanguageMgr_ParseMask` | Starts from "dev". With the file's data, it splits it into lines (`f_810c4420`, CRLF), parses one token with `f_810c4d60` into `+16`, then sets `+64` to 1 | read |
| `f_810c4d30` | `RegionLanguageMgr_GetVersion` | `+16` when `+64` is set, otherwise the default at `0x83f44d10` (0x7fffffff). About 40 functions call it | read |
| `f_810c4d60` | `ReleaseVersion_Parse(text)` | A release-version token to a number: "dev" gives 0x7fffffff, otherwise, **guess**, the digits (so "120" is 120) | read |
| `f_810c4bf0` | `ReleaseVersion_Init120` | Stores `ReleaseVersion_Parse("120")` at `0x84e49438`, the threshold for the ".120" resources. `f_810c4c90` compares the version with "110" the same way | read |

Earlier notes called this value a region code. It is a release version that the game keeps in `RegionLangMask.txt`. On
"dev", `f_83be64a0` also picks the "All" layout variants instead of "Product". **read**

### The sequence nodes

The title's flow and the new-game flow are sequence nodes: classes in the code, wired together by `.ainb` files in the
RomFS. Every node's vtable has the same shape: slot 19 (`+152`) is its name, slot 28 (`+224`) declares its parameters, and
slot 39 (`+312`) evaluates it. A static initialiser (for this query `f_8108d7c0`) fills a 32-byte registry entry with a
u32 key and the create function, and `f_80ca4f70` links it into the registry. The key is not murmur3, CRC32 or FNV-1a of the
class name. **read** (the key's hash: unknown)

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8331a6c0` | `SeqQueryHasAoCMQ_Create` | Allocates the node (40 bytes) with `f_8005ebe0`, zeroes `+8` to `+39`, sets the u16 at `+30` to 0xff and the vtable to `0x84bea500`. Its registry entry is `0x84e92f98`, key 0x97bb1b66 | read |
| `f_8331a8f0` | `SeqQueryHasAoCMQ_Name` | The node's name (slot 19, at `0x84bea598`) | read |
| `f_8331a800` | `SeqQueryHasAoCMQ_Output` | Declares the output "HasAoCMQ" (slot 28, at `0x84bea5e0`). `f_803f5b40(node, &name, node+32, 1)` binds it to `+32` | read |
| `f_8331a710` | `SeqQueryHasAoCMQ_Evaluate` | Slot 39 (at `0x84bea638`). HasAoCMQ is 1 when the release version (as `f_810c4d30` reads it) is 0x7fffffff: "dev", or the manager missing or not yet loaded. Otherwise it is bit 0 of `[[0x84e97e48]+216]`. It writes the byte to `[+32] & ~3` and returns nothing in `x0`: the answer is only the output. When bit 1 of `[+32]` is set and the byte changes, it reads a TLS value (`f_851095f0`) and, if its bit 31 is set, writes 0x80000001 back (`f_85109610`) | read |
| `f_851095f0` | `nn::os::GetTlsValue` | The slot number is at `[[0x84c9db18]]+84` (`0x84c9db18` holds `0x84ea52a8`) | read |
| `f_85109610` | `nn::os::SetTlsValue` | The same slot | read |
| `f_8331cdb0` | `SeqQueryIsExistSave_Evaluate` | Outputs "IsExist" and "IsMQModeExist" from the caption manager (`[0x84e98e38]` `+142`, `+143`). It only reports; it enables nothing | read |
| `f_80c6e850` | `SeqQueryIsExistSave_Output` | Declares those two outputs | read |
| `f_83305480` | `SeqOneShotSetMQMode_Evaluate` | Inputs: IsIgnoreSequenceCheckFlag (`+32`) and Bool (`+40`). When Bool is set and the entitlement (`[0x84c9d9c8]`, which points to `0x84e97e48`) is null or has bit 0 of `+216` set, the mode is 1. When Bool is set and the add-on is not owned, it clears `IsLastSelectedMQMode` (`f_806a2b20`, [game-data.md](game-data.md)) and the mode is 0. When Bool is clear, the mode is 0. It then stores the mode at a component's `+336` (and 1 at `+337`), at the caption manager's `+145` (which picks the Master Quest captions, [save-data.md](save-data.md)) and in bit 23 of `[[0x84e5e258]+488]+3616`, then calls `f_83381400([0x84e966e8])` | read |
| `f_833056c0` | `SeqOneShotSetMQMode_Name` | The node's name, "SeqOneShotSetMQMode" (`0x83e1662c`) | read |
| `f_832f8f80` | `SeqOneShotOpenSystemWindow_Evaluate` | Opens a system window. With IsTitle (`+48`) set it uses `TitleSystemWindow_00` (murmur3 0xe1932d84), otherwise `SystemWindow_00` (0x6d47deab). It matches Preset (`+32`) against the names at `0x83f0a9a0` to get NewGame 0, ResetOption 1, BackToTitle 2, BackToTitleFromGameOver 3, **NewGameMQ 4** or **ReleaseMQ 5**, then calls `f_83425a00` | read |
| `f_82919de0` | `OneShotOpenSystemWindow_Evaluate` | The AI-side node that opens the same windows, with no IsTitle | read |
| `f_83425a00` | `SystemWindow_Open(screen, preset, isTitle)` | Stores isTitle at `+712` and the preset at `+716`, sets SLink property 7, and puts the preset's message (`%04d` in `LayoutMsg/SystemWindow_00`) in `T_Text_00`. Buttons (`f_83425bc0`): presets 0 and 4 get (0,0) then (1,1); 1 gets (0,2); 2 and 3 get (0,3); 5 gets only (0,4), at animation frame 1.0 instead of 2.0 (**guess:** a one-button notice). It shows text and buttons and sets no Master Quest state | read |

### Master Quest mode

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_811f4040` | `GameData_IsLastSelectedMQMode` | Reads the bool `IsLastSelectedMQMode` (0x605e5df8) from the game-data manager (`[0x84ea42a8]`): Master Quest mode as the rest of the game sees it. About 16 more functions read the hash directly (among them `f_8022b900`, `f_8022a580`, `f_80594ab0`), not yet looked at | read |
| `f_82a21720` | `QueryGameBancParamMQName_Evaluate` | An AI query node (declared in `AI/NodeDefinition/Node.Product.111.aidefn` with one input, "GameBancParamName"): copies its input "GameBancParamName" into the string at `+32` (capacity `+40`) and, in Master Quest mode, appends "_MQ" (`0x83d0488a`) when the name is one of the 13 dungeons: InsideTheDekuTree, DodongoCavern, InsideJabuJabuBelly, ForestTemple, FireTemple, WaterTemple, ShadowTemple, SpiritTemple, BottomOfTheWell, IceCavern, GerudoTrainingGround, CrumblingGanonCastle, InsideGanonCastle. No code reads its output, and no shipped `.ainb` (the 1,942 loose ones, the 749 in the AI.Global pack, those in the Forest Temple's actor packs) uses the node. The name is the key of a map's `GameBancParam` ([maps.md](maps.md)). Its name function is `f_82a21d80` ("QueryGameBancParamMQName") | read |
| `f_80ecffd0` | `QueryGameBancParamMQName_Create` | Makes the node (0x148 bytes, vtable `0x84aa73e8`) | read |
| `f_80831a80` | `QueryGameBancParamMQName_Params` | Slot 28: declares the input "GameBancParamName" (`0x83d9e26a`) at `+304` and an output of the same name at `+320` | read |
| `f_809d76a0` | `Banc_SetIsMQMap` | On a new map (vtable `0x84bda7b0`): sets `Sequence_IsChildhood` (0xcea220d3) from the map's `GameBancParam` (`+842`, [maps.md](maps.md)), unless `DebugParam.IsSetChildhoodManually` is set, and when the map's name contains "_MQ", sets `DebugParam.IsEnterMQScene` (0x3c3171e5, [game-data.md](game-data.md)) to true | read |

Two map functions on [maps.md](maps.md) look at the mode too: `OptimizeChangeBanc_AddUnit` (`f_810a44e0`) inserts "_MQ"
into a preloaded unit's layout path when the unit's HasMQ is set, and `BancBasedResourceMgr_Load` (`f_80e55d20`) picks the
".120" tables by the add-on byte. The dungeon map screen (`f_8113b7a0`) reads the mode as well. **read**

`f_829df380` ([events.md](events.md), *The next map's name*) builds the next map's name for a change of map: in Master
Quest mode it appends "_MQ" to every next map whose name has no "_Child" or "_Adult" and isn't FinalBattleField, HyruleCastle
or GanonCastle, reading `IsLastSelectedMQMode` straight from the game-data manager. **read** The saves keep
"ContinuousAdultStatusMQ" and "ContinuousChildStatusMQ" apart from the normal ones (`f_810c6ed0`, `f_833c03f0`,
`f_833c0770`). `f_833f89b0` moves a UI animation 11 frames on in Master Quest mode. **read**

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84e97e48` | `g_Entitlement` | The entitlement object's holder; `+216` is 1 when add-on 1 is owned | read |
| `0x84c9d9c8` | `got_Entitlement` | Its GOT slot: holds `0x84e97e48` (how `f_83305480` and `f_80e55d20` reach it) | read |
| `0x84eb5f40` | `g_EntitlementBoot` | `nnMain`'s copy of the entitlement pointer, through which the boot sends its phases | read |
| `0x84bf86d0` | `Entitlement_vtable` | The entitlement object's vtable; slot 5 is `f_83392a00` | read |
| `0x84e9f558` | `g_RegionLanguageMgr` | The region and language manager's holder | read |
| `0x83f44d10` | `ReleaseVersion_Default` | 0x7fffffff, the version while `RegionLangMask.txt` is not loaded (as "dev") | read |
| `0x84e49438` | `g_ReleaseVersion120` | The parsed "120", the threshold for the ".120" resources | read |
| `0x84e92f98` | `SeqQueryHasAoCMQ_Entry` | SeqQueryHasAoCMQ's registry entry: key 0x97bb1b66 at `+0`, create function `f_8331a6c0` at `+8` | read |
| `0x84bea500` | `SeqQueryHasAoCMQ_vtable` | Its vtable; the name, output and evaluate slots are at `0x84bea598`, `0x84bea5e0` and `0x84bea638` | read |
| `0x84bd4be8` | `SeqOneShotOpenSystemWindow_vtable` | SeqOneShotOpenSystemWindow's vtable (its evaluate slot is at `0x84bd4d20`) | read |
| `0x83f0a9a0` | `SystemWindowPresetNames` | The system windows' preset names | read |
| `0x83d0488a` | `str_MQ` | "_MQ" | read |

## Structures

### Entitlement object (0x100 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0` | Vtable `0x84bf86d0` | read |
| `+8` | A base part, with a light event at `+120` | read |
| `+144`, `+176` | Two more vtables, filled at run time | read |
| `+208` | 1 or 2, set by slot 6 (`f_83392c80`); `f_8007f6c0`, `f_8007fe60`, `f_800eb9c0`, `f_832d11b0` and `f_832d1660` test it for 2 | read |
| `+212` | Bit 0, set by those when `+208` is 2, cleared by slot 6 | read |
| `+216` | 1 when add-on 1 is owned (set at boot by `f_83392a00`, never cleared) | read |
| `+224` | A disposer, also kept at `0x84e97bc0` | read |

### Region and language manager (0x68 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+16` | The release version parsed from `RegionLangMask.txt` | read |
| `+64` | 1 once `+16` is set | read |

### SeqQueryHasAoCMQ (40 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0` | Vtable `0x84bea500` | read |
| `+30` | A u16, 0xff | read |
| `+32` | The output "HasAoCMQ": a pointer to its byte, tagged in its low 2 bits. With bit 1 set, a change of the byte raises the sequence's TLS flag (**guess**: bit 1 means something watches the output) | read |

## Notes

- In Master Quest mode the camera's input query (`f_805965e0`, [input.md](input.md)) negates the X of both sticks and six motion values, which suggests a mirrored world. **guess** (from the input code)
- The region and language manager's `+0` holds the region-language type (0-17), written by `f_80fb1600` through `f_8349b3f0` from the list in `RegionLangMask.txt`; in this dump its first line lists 17 codes, all but `EUpt` ([audio.md](audio.md)). **read**
- The network library mounts the RomFS a second time, as `_npln` (`f_821376a0`, `f_82136380`; [resources.md](resources.md)). **read**
### What the title's data uses

The node wiring lives in the RomFS's `.ainb` files. A search of the 1942 uncompressed ones, by node and parameter name:
**tested**

- **SeqQueryHasAoCMQ:** no shipped `.ainb` uses it, so in this data nothing asks the query.
- **NewGameMQ:** only `Sequence/ConfirmationNewGame.module.ainb`, which takes an `IsMQ` input and picks the preset from it.
- **ReleaseMQ:** no `.ainb`.
- **IsLastSelectedMQMode:** read by `BeforeInitializeCommon`, `NormalTitle`, `LoadList`, `TitleToMainWithLoadSave` and
  `_LocalModule_fd9440c9851e`, which pass it to SeqOneShotSetMQMode. So the last mode is restored at boot and checked against
  ownership again.
- **OpenTitle** uses `SeqQueryIsExistSave` and `SeqOneShotSetSLinkPropertyTitleBtnArrangementType` (`f_833088a0` →
  `f_83427000`, which only sets sound-link property 7 on `Title_00`).
- **NormalTitle** uses Pa_Button_00 … 02 on `Title_00`, IsMQ, IsLastSelectedMQMode, IsExist, IsMQModeExist,
  `SeqOneShotSetGameDataBool` and `UI_Debug_IsOpenedTitle`.

The compressed data, searched too: **tested**

- **The AI.Global pack** (749 `.ainb`): none of HasAoCMQ, SeqQueryHasAoCMQ, ReleaseMQ, NewGameMQ, IsMQ,
  IsLastSelectedMQMode, QueryGameBancParamMQName or SetMQMode.
- **The Title layout archive** (`Title_00`, `TitleMenuBG_00`, `PaCommonBtnTitle_00`): no pane or string about Master Quest.
  `Title_00` has four buttons, `Pa_Button_00` … `03`; `OpenTitle` gives text only to `00` … `02`, and no title graph uses
  `03`.
- **Two more nodes with an IsMQ input:** `SeqOneShotOnTitleBancOptimize` (used by NormalTitle) and
  `SeqOneShotOnLoadListBancOptimize` (used by LoadList), declared in the Sequence node definitions.

So the title has no Master Quest button in this data: the choice is most likely the NewGameMQ window that
`ConfirmationNewGame` opens from its IsMQ input. **guess** (the graphs' wiring was not parsed)

### Why LS never offers it

LS doesn't implement `nn::aoc`. Unimplemented imports go to `a64_unimplemented`
(`third_party/a64-kit/rt/a64_rt.c`), which logs the import once and returns 0 in `x0`. The log shows the
boot check: `unimplemented import _ZN2nn3aoc17CountAddOnContentEv (lr 0x83392a38)`. A count of 0 skips the list call,
`+216` stays 0, and `SeqOneShotSetMQMode` refuses Master Quest mode. **tested** (the log), **read** (the rest)

### What an nn::aoc implementation must return

- **The calls:** `CountAddOnContent` and `ListAddOnContent(out, offset, count)` are the only add-on imports. There is no
  `MountAddOnContent`, `PrepareAddOnContent`, `GetAddOnContentBaseId`, `GetAddOnContentListChangedEvent`, `nn::ec` or
  `nn::fs` add-on mount. **read**
- **The index:** Master Quest is add-on index **1**. `CountAddOnContent()` returns how many add-ons the player has.
  `ListAddOnContent(out, 0, n)` writes their indices into `out`, up to `n` (the game asks for at most 32). **read**
- **When:** once, in boot phase 2, before the title. The game's `nn::oe` message loop (`f_83be0230`) handles messages 4, 15,
  16, 30, 31, 92 and 93, none about add-ons, so an add-on that appears later is not seen until the next start. **read**
- **What follows:** with the add-on reported and a release version of at least 120, `f_80e55d20` asks for the 7
  `Banc/BancBasedResource/*.120.byml` files, which this dump doesn't have. The retail `System/RegionLangMask.txt` has four
  lines (the regions and languages, `Normal`, `111` and a hash), and `f_80fb1600` parses the third (through `f_810c4420`):
  the release version is 111. That is at least 110 but below 120, so a retail run of this dump never asks for the ".120"
  tables. **read** (the parse), **tested** (the file)
- **Only for owners:** report add-on 1 only when the player's own add-on dump is found (as emulators do). The
  development-version path in `f_8331a710` and the null-holder path in `f_83305480` are the game's own and are not routes
  for LS to use. Making the check pass for everyone would unlock paid content without a licence, and LS doesn't
  do that.

### Is it extra data or only a licence?

For the code it is a licence: the game imports no add-on data access, and all of Master Quest's selection logic is in the
base program. The Master Quest maps themselves are not in the dump looked at. **read** (the imports), **tested** (the files)

- **No way to read add-on data:** the only mounts the game imports are `MountRom` (its own RomFS), save data, cache storage
  and temporary storage. The only "AoC" strings in the game's own data are the query's names (`SeqQueryHasAoCMQ`,
  `HasAoCMQ`); `aoc:` (`0x85ba030a`) is in the SDK's part of the image, the system service's name. So this program reads
  everything, `AoC1/` included, from its own RomFS. **read**
- **The dump:** the base game (v65536) and an update (v131072). No add-on title. **tested**
- **`AoC1/Banc/BancBasedResource/`:** both RomFS have it, with the same 7 tables as `Banc/BancBasedResource/`, each 5 to 30%
  larger. Decompressed (with the dictionaries in `Pack/ZsDic.pack.zs`) and compared by their strings, each holds the base
  table's entries plus entries for the Master Quest maps: StartPos and LocationArea all 13 `<Dungeon>_MQ` maps, MapParam 12
  (no `CrumblingGanonCastle_MQ`), NoEscapeRangeArea 9 and GmdReset 4 (Deku Tree, Dodongo's Cavern, Jabu-Jabu, Forest
  Temple). AlternativeAnchor and StaticGameDataArea are empty in both. No code string or constant forms the `AoC1` prefix, and the manager's string at `+0` can't add it: it is the format's
  third argument, a suffix before ".byml", so every table path starts with `Banc/` ([maps.md](maps.md)). So `AoC1/` would
  come from the file layer, or the program doesn't use those tables. **guess** (The image holds the names of more `nn::aoc`
  functions, such as `PrepareAddOnContent` and `GetRegisteredAddOnContentPath`, probably the SDK's own symbols; the game
  imports only the two. **guess**)
  **tested** (the files), open (what reads them)
- **What the `_MQ` entries hold** (parsed and compared with each dungeon's normal entry): **tested**
  - **LocationArea:** the same location files as the normal dungeon (`Work/Location/<Dungeon>.game__location__Location.gyml`),
    with a different `Dungeon` list (location names and point lists).
  - **StartPos:** the same named start points, about half of them moved. The Spirit Temple's are identical.
  - **NoEscapeRangeArea:** the event areas (`NoEscapeArea_SageOfShadow` and the like), unchanged in 4 of the 9 dungeons and
    moved in the rest.
  - **GmdReset:** for 4 dungeons, a different set of game-data hashes to reset.
  - **MapParam:** the dungeon map screen (floors, rooms, doors, chests `TBox`, markers): the same floor counts, with doors and
    chests in other places and other numbers (the Shadow Temple 14 chests normally, 21 in Master Quest; Dodongo's Cavern's
    entry has only `CollectSign`, `EntranceAnchor`, `Facility` and `MapMode`).
  - **No file names** in any `_MQ` entry except the shared location files.

  So Master Quest reuses the normal dungeons' locations and, most likely, their geometry. What it needs that the dump lacks
  is the `<Dungeon>_MQ` maps' own layouts (actors, chests, doors), not new terrain. **read** (from the tables), **guess**
  (the geometry)
- **The maps:** no RomFS file has `_MQ` in its name, and each dungeon folder under `Banc/` holds only its normal map. The
  update's resource size table lists names this dump lacks: all 7 `*.120.byml`, `AoC1/Banc/GameBancParam/…_MQ…`,
  `AoC1/Model/…_MQ…bfres`, `AoC1/Tex/…_MQ.bntx`, `AoC1/TerrainArc/…_MQ…`, `…_MQ.bcul` and Master Quest voice files
  (matched by CRC32 against guessed names). No Master Quest dungeon map or layout name matched. **tested**
- **The ".120" tables are a different revision.** The update's resource size table gives every table's size (the file's
  size plus about 264 bytes, checked against the base and `AoC1/` files). The ".120" tables, which this dump lacks, are
  bigger than the base ones but are not the `AoC1/` ones: MapParam 187,592 against 155,656 for `AoC1/` and 118,856 for the
  base; GmdReset 13,640 / 12,616 / 11,368; LocationArea 13,480 / 13,352 / 11,752; StartPos 26,760 / 27,048 / 25,512;
  NoEscapeRangeArea 7,080 / 7,304 / 6,568 (AlternativeAnchor and StaticGameDataArea are 296 in all three). **tested**
  So the Master Quest entries already ship in the base game's `AoC1/` tables, which this program never reads (its table
  path always starts with `Banc/`), and the tables it would read for Master Quest are the ".120" ones of data version 1.2.0,
  a later revision. Their much larger MapParam would fit the Master Quest map-screen entries being filled in (the `AoC1/`
  ones list 1 room against the normal dungeons' 29, and Dodongo's Cavern's is only partly filled). **guess**
- **The `AoC1/` tables are the base tables plus the Master Quest entries, nothing else.** In all five that differ,
  every normal map's entry is identical to the base one, and the only extra entries are the `_MQ` maps; both are BYML
  version 7. **tested** So each version's size above the base is its Master Quest part (plus, for ".120", any other
  change): MapParam +36,800 in `AoC1/` against +68,736 in ".120", GmdReset +1,248 / +2,272, LocationArea +1,600 / +1,728,
  StartPos +1,536 / +1,248, NoEscapeRangeArea +736 / +512. **tested** (the sizes) The ".120" tables grow in some tables and
  shrink in others, so 1.2.0 revised the Master Quest entries rather than only adding to them: perhaps filled-in map-screen
  rooms, more flags to reset, and start points or event areas dropped or merged. It may have changed some normal maps'
  entries too. **guess** (the files that would show it are not in the dump)
- **Only the per-map tables have a ".120" revision.** Every file the base game and the update have (186,808) was tested
  against the update's size table with ".120" put in at each "." of its name, and with an `AoC1/` prefix: about 490,000
  names, where about 25 would match by chance. The 7 `Banc/BancBasedResource` tables match in both forms. The other 39
  matches are scattered single files (a few `TerrainArc` bakes, `PreComputedCulling` entries, a sound, a texture), several
  with ".120" in a place no name would have it (`0100000003.120.dream.bake_ao.extm`): chance matches. The earlier
  `AoC1/Model/…_MQ…bfres` matches were at chance level too. **tested**
- **So:** the Master Quest maps' index entries and layouts most likely come with a later game update (data version 1.2.0, the ".120"), in its RomFS,
  since this program can't mount add-on data. A player would need that update dumped as well as the add-on's licence.
  **guess**

### How a Master Quest dungeon loads, and whether its geometry is new

How a map loads in general is on [maps.md](maps.md). What Master Quest changes:

- **It is another map.** A map's name is the key of its `GameBancParam` in the "GameBancInfo" table, so a `<Dungeon>_MQ`
  name loads its own index entry and, through its `Banc.Main`, its own layout. **read** Two functions add "_MQ" to a map's
  name: `f_829df380`, which builds the next map's name for a change of map ([events.md](events.md)), appending "_MQ" to
  any next map without an age suffix except the three castle maps, and the AI node `QueryGameBancParamMQName` (`f_82a21720`), which no shipped graph uses. Once on an
  `_MQ` map, `f_809d76a0` sets `DebugParam.IsEnterMQScene`. **read**
- **Preloads ask for the `_MQ` layout.** The units that preload a dungeon from the map outside it carry HasMQ, and in Master
  Quest mode `f_810a44e0` turns their layout path into `<Dungeon>_MQ.bcett.json` in the same folder. HasMQ is set on 23
  units in 23 maps, together covering all 13 dungeons (below). **read** (the code), **tested** (the files)
- **No loader of geometry looks at the mode.** The static collision, terrain, navigation mesh and volume statistics
  loaders ([maps.md](maps.md)) never read Master Quest mode, HasMQ or "_MQ". The static collision's file is named from
  `Banc.Main`, so a `<Dungeon>_MQ` layout implies its own collision file; terrain and the navigation mesh are named by the
  `GameBancParam`, which can point at the normal dungeon's. **read**
- **The geometry is placed by the layout.** A dungeon's rooms are `Land_*` actors in its layout files
  ([maps.md](maps.md)), so the Master Quest layout decides which room pieces load. **tested** (the normal layouts)
- **The `_MQ` tables index another layout.** None of the 51 `InstanceID`s in the Forest Temple's Master Quest MapParam
  entry, nor its 2 room entrances, is an actor in the normal layout, not even where an item sits at exactly the same spot.
  The normal entry's 43 all are. So the Master Quest tables were baked from a separate layout with its own actors. **tested**
- **No Master Quest room pieces are listed.** Of the Forest Temple's 120 `Land_*` actor kinds, no `_MQ` variant (with or
  without `AoC1/`, as a pack, model or collision shape: 2,700 names) is in the update's resource size table, where about
  0.14 would match by chance. Nor are the Master Quest dungeons' `GameBancParam`s, layouts, static collision or navigation
  meshes, under the names the code builds (about 330 names, by the agent that traced the loaders). A few `_MQ` model
  names did match (such as `Model/Land_InsideJabu_Jabu_Cliff_A_MQ…bfres`), but among guesses where about 10 chance matches
  were expected. **tested**

So Master Quest is not built from the normal dungeon at run time: it loads its own map and layout. Those layouts most
likely place the normal dungeons' room pieces again, with their own doors, chests, enemies and puzzles. That is a **guess**,
fairly sure: nothing in the code forces it, and the files that would settle it are not in the dump.

### The 23 units with HasMQ

Each is a map's preload unit pointing at a dungeon, one per map. **tested**

| Dungeon | Maps whose unit has HasMQ |
|---|---|
| InsideTheDekuTree | KokiriForest_Adult, KokiriForest_Child |
| DodongoCavern | DeathMountainTrail_Adult, DeathMountainTrail_Child |
| InsideJabuJabuBelly | ZoraFountain_Child |
| ForestTemple | SacredForestMeadow_Adult, SacredForestMeadow_Child |
| FireTemple | DeathMountainCrater_Adult, DeathMountainCrater_Child |
| WaterTemple | HyruleField_Adult, HyruleField_Child |
| ShadowTemple | Graveyard_Adult, Graveyard_Child |
| SpiritTemple | DesertColossus_Adult, DesertColossus_Child |
| BottomOfTheWell | KakarikoVillage_Adult, KakarikoVillage_Adult_Alternative, KakarikoVillage_Child |
| IceCavern | ZoraFountain_Adult |
| GerudoTrainingGround | GerudoFortress_Adult |
| InsideGanonCastle | GanonCastle |
| CrumblingGanonCastle | GanondorfStage, InsideGanonCastle |

Two of the holders are inside Ganon's castle itself (InsideGanonCastle and GanondorfStage), so not all are outside the
dungeons.

### Master Quest saves

- **Slots:** a Master Quest playthrough saves to the slot number plus 6, so Master Quest slots 0 … 5 are the folders
  `slot_06` … `slot_11`. The offset is added in `f_832e1db0` (load), `f_80cdc350` (captions) and `f_811f4910` (save slot)
  ([save-data.md](save-data.md)). **read**
- **Captions:** the caption manager keeps a second array of 6 for Master Quest (`+72`, buffer `+88`), and `+145`, set by
  `f_83305480`, picks it. **read**

## Open questions

- The `.ainb` graphs' wiring: which node feeds which in `ConfirmationNewGame`, NormalTitle and LoadList (no `.ainb` parser
  yet).
- Whether the region and language manager is loaded before the title asks (a live read of `[0x84e9f558]` `+16` and `+64`).
- Where the `AoC1/` tables could be read from, if at all.
- Which maps `f_829df380` is used for: it would add "_MQ" to any age-less map but the three castle maps, so boss rooms and
  other age-less maps reached through it would need `_MQ` entries too ([events.md](events.md)).
- The sequence node registry's key hash (0x97bb1b66 for SeqQueryHasAoCMQ; about 190,000 strings tried), and the name of
  struct 0x42b90553 ([game-data.md](game-data.md)).
