# Game data and the save files

How the game keeps its flags and values ("game data"), how code reads them, and what the save files hold. Looked at while
looking for a way to try Master Quest (November 2026), and for the debug tools' flag reads and writes.

**Longshot code:** [host/hos_main.c](../../host/hos_main.c) (`oot_gamedata_bool`, `oot_gamedata_set_bool`)
**See also:** [save-data.md](save-data.md), [master-quest.md](master-quest.md)

## How the game declares its values

The game's values are declared in `GameData/GameDataList.Product.110.byml.zs` in the RomFS: a zstd-compressed BYML, using the
dictionary in `Pack/ZsDic.pack.zs`. **tested**

- **`Data`** holds one table per type. Each entry has a `Hash`, a `DefaultValue`, a `ResetTypeValue` and a `SaveFileIndex`.

  | Type | Entries |
  |---|---|
  | Bool | 11292 |
  | Bool64bitKey | 11657 |
  | BoolExp | 884 |
  | Enum | 130 |
  | Float | 89 |
  | Int | 1259 |
  | Int64 | 33 |
  | Struct | 874 |
  | UInt | 97 |
  | Vector3 | 26 |
  | String / WString | a few |

  There are also array forms of most types.

- **`SaveFileIndex`** says which file a value is saved in:

  | Index | File |
  |---|---|
  | 0 | `slot_NN/progress.sav` |
  | 1 | `option.sav` |
  | 2 | `slot_NN/caption.sav` |
  | 3 | `account.sav` |

- **`MetaData`** holds `SaveDirectory`, the file names (`slot_%02d/progress.sav`, `option.sav`, `slot_%02d/caption.sav`,
  `account.sav`), plus `SaveDataSize` (703884, 44660, 42356, 320 bytes) and the files' offsets.
- **Flag names are not in the game.** A flag is known only by the murmur3 (x86, 32-bit, seed 0) hash of its name; the hash
  function was checked against a known name, `SelectedCaptionIndex` → `0x1a49c4f6`. Hashing every identifier in the
  executable (163,375 strings) matched neither account flag below, so the names live in data, if anywhere. **tested**
- **The code reads values by hash**, through the game-data manager, with the hash built in registers. Look for MOVZ/MOVK
  pairs such as `0x5097` / `0xa299` (that is, 0xa2995097). **read**

## Functions

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80cbf5e0` | `GameDataMgr_ReadFileList` | Reads the file list (`SaveDirectory`) into the game-data manager (`[0x84ea42a8]`) | read |
| `f_8022d9c0` | `GameDataMgr_GetBool` | A bool by its name's hash: the `(hash, index)` table at `mgr+32` (its size at `+44`), then the bools' 24-byte entries at `mgr+24` (their count at `+16`), the value at `+22` of an entry | tested |
| `f_80cd0ea0` | `GameDataMgr_GetString64` | A String64 by its name's hash | tested |
| `f_809d3eb0` | `GameDataMgr_StructField` | With `f_809d39e0`, writes a struct's field by hash (the save list's CaptionData pick, [save-list.md](save-list.md)); which of the two finds the field and which writes it is not known | guess |
| `f_809d39e0` | `GameDataMgr_SetStructField` | The other half of that write | guess |
| `f_806a2b20` | `GameDataMgr_SetBool(mgr, value, hash)` | Builds a `{value, hash}` request (the byte at `+0`, the hash at `+4`) and passes it to a virtual call on the manager. `SeqOneShotSetMQMode` clears `IsLastSelectedMQMode` with it ([master-quest.md](master-quest.md)) | guess |
| `f_83051aa0` | `GameClear_SetFlags` | At the game's end: sets `IsGameClear`, `IsGameClearAtLeastOnce`, CaptionData's `IsClear` and the `DefeatedEnemyNum` fields for Ganon and Ganondorf. No direct caller found | read |
| `f_80857070` | `GameDataMgr_GetInt` | Reads an int by hash (the save list's last picked index, 0xa2995097) | read |

## Known values

| Hash | Type | What it is | Sure |
|---|---|---|---|
| `0x1a49c4f6` | | `SelectedCaptionIndex` | tested |
| `0xef2b60c2` | Bool | `Sequence_IsLoadingBanc`: a map is loading | tested |
| `0xcea220d3` | Bool | `Sequence_IsChildhood`: Link is a child | tested |
| `0xa2995097` | Int | The save list's last picked index | tested |
| `0x46926d32` | Struct | CaptionData; its field `0xd13d898b` is the picked slot | read |
| `0x605e5df8` | Bool | `IsLastSelectedMQMode` (murmur3 checked): Master Quest mode. In `account.sav`; restored at boot and cleared when the add-on is not owned ([master-quest.md](master-quest.md)) | tested |
| `0x5526db84` | Bool | `IsGameClearAtLeastOnce`, in `account.sav`; set with `IsGameClear` when the game is cleared (`f_83051aa0`) | tested |
| `0xb4962fee` | Bool | `IsGameClear` | tested |
| `0x8b95f6eb` | Bool | `IsClear`, a field of CaptionData | tested |
| `0xfb4b52f6` | Struct | `DefeatedEnemyNum`; its fields include `Enemy_DungeonBoss_Ganon` (0x2b9afb24) and `Enemy_DungeonBoss_Ganondorf` (0x367c8cc6), also fields of struct 0x42b90553 (name not found) | tested |
| `0x58131bc2` | Struct | `DebugParam`, not saved (SaveFileIndex −1). Fields found: `IsEnterMQScene` (0x3c3171e5; the full name `DebugParam.IsEnterMQScene` is 0x11b16e68), `IsSetChildhoodManually` (0x078ef520), IsSkipForceResetGameData, IsGameDataAllResetOnSceneDestroy, IsOverturnTerrainSetupAndLoadBanc, IsRequestedResetGimmick, IsOverrideTraversePos, IsKeepLoading | tested |
| `0xbe9f04bd` | | `Account`: GameDataList's SaveTypeHash for `account.sav` (`Option` is 0xa60ba395, `Caption` 0x26f3523b) | tested |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84ea42a8` | `g_GameDataBlackboard` | The game-data manager as the save code loads it (`[0x84ea42a8]`): the file list, CaptionData | read |
| `0x84c9e180` | `g_GameDataMgrHolder` | The game-data manager as ReserveBancChange's readers load it (`[[0x84c9e180]]`); the debug tools read and write flags through it | tested |

## The save files

Each profile folder holds `account.sav`, `option.sav` and `slot_00 … slot_NN` with `progress.sav` and `caption.sav` in each
(see [../saves.md](../saves.md)). The files are flat lists of values keyed by hash, in the same family as Tears of the
Kingdom's saves:

- A header: `04 03 02 01`, then a version word (`0x002d80eb`) and the file's size.
- The values: a u32 hash followed by its u32 value, for the simple types.

**tested** (account.sav)

### account.sav (320 bytes)

GameDataList declares only two values for it (`SaveFileIndex` 3), both Bool, both false by default:

| Offset | Hash | Value in our saves |
|---|---|---|
| `0x28` | `0x605e5df8` | 0 |
| `0x30` | `0x5526db84` | 0 |

Then an array of 33 entries (0 … 32), each `{0, index}`: an account-wide array whose purpose is unknown. The file ends with
`0xbe9f04bd` followed by 0: `Account`, the file's SaveTypeHash in GameDataList (murmur3 checked), not a value. **tested**

**The two bools are `IsLastSelectedMQMode` (0x605e5df8) and `IsGameClearAtLeastOnce` (0x5526db84)** (murmur3 checked against
names from the game's data). Setting either or both to 1 in a test copy changed nothing on the title screen, and no Master
Quest option appeared. **tested** That fits the code: `SeqOneShotSetMQMode` clears `IsLastSelectedMQMode` when the add-on is
not owned ([master-quest.md](master-quest.md)), and `IsGameClearAtLeastOnce` is set at the game's end. **read**


## Notes

- More known hashes: `OptionData` 0x834d9d9e, `VoiceLanguage` 0x74b1edd9, `OptionData.VoiceLanguage` 0xa2dcdc9d (the voice language option, default Japanese: [audio.md](audio.md)); `f_809d3eb0` and `f_809d3ba0` are used there as a find-struct / set-enum-field pair. **read**

## Open questions

- Are `[0x84ea42a8]` and `[[0x84c9e180]]` the same manager, reached two ways, or two objects? The save code and the host's
  flag reads each use one.
- What the 33-entry array in `account.sav` holds.
