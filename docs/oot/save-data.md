# Save files, slots and captions

Where a save goes: the files and their slots, the worker that reads and writes them, and the caption manager that holds what
the save list shows of each slot (place, date, screenshot, hearts, medallions). Looked at for the save slots work (November
2026).

**LS code:** `host/nn_fs.c` (the `save:` mount)
**See also:** [save-list.md](save-list.md), [game-data.md](game-data.md) (what is in the files),
[master-quest.md](master-quest.md) (its slots), `docs/saves.md` (where LS keeps saves)

## The caption manager

A singleton (`[0x84e98e38]`) holding each slot's caption.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80ee9370` | `CaptionMgr_Create` | Makes the manager (0xb8 bytes) | read |
| `f_80ee8d00` | `CaptionMgr_Construct` | Constructs it: two arrays `{num, max, ptrs}` of **6**, inline (see [Structures](#structures)) | read |
| `f_80ee8bf0` | `CaptionArray_Init(arr, 6, buffer)` | Sets an array's buffer and capacity | read |
| `f_80bd00c0` | `CaptionMgr_MakeRecords` | Makes the records (0x5a0 bytes each), up to each array's capacity | read |
| `f_80bcff10` | `CaptionRecord_Init(rec, slot, mq)` | Sets `rec+4` = slot and `rec+1434` = Master Quest | read |
| `f_833cd490` | `CaptionMgr_Record(mgr, &rec, i)` | The record of slot `i`. Fails unless the manager is ready (`+140 == 4`), every record is loaded (state 5) and `i < num` | tested |
| `f_833cd530` | `CaptionMgr_RecordAt` | A record by index | read |
| `f_80505420` | `CaptionMgr_LoadStep` | The manager's load state machine | read |
| `f_80cdc350` | `CaptionMgr_RequestFile` | Asks for a caption's file (Master Quest adds 6 to the slot) | read |
| `f_833cd5d0` | `CaptionMgr_LatestIndex` | The latest save's index (copies into a local array of **6**) | read |
| `f_833cd680` | `CaptionMgr_LatestIndex2` | The same, alongside `f_833cd5d0` | read |

## Save files and slots

The file names are not in the code: they come from the game data's `SaveDirectory` ([game-data.md](game-data.md)):
`slot_%02d/progress.sav`, `option.sav`, `slot_%02d/caption.sav`, `account.sav`.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80e00260` | `SaveWorker_Run` | The save worker's commands ('read', 'size', 'writ', 'mcit' = commit) | read |
| `f_806a3aa0` | `SaveFile_Write` | Writes a file: the name formatted with the slot (`f_80062b70`) and posted to the worker | read |
| `f_83051c60` | `SaveFile_Read` | Reads a file the same way | read |
| `f_811f4910` | `Save_SetSlot` | Sets which slot a save writes (Master Quest adds 6) | read |
| `f_830506a0` | `Save_ChooseSlot` | Chooses the slot for a save: the saves sorted by time, the first free one of **0…5** | read |
| `f_83310630` | `SeqQueryGameDataGetManualSaveIndex` | The manual save's index: **6** places, -1 if none | read |
| `f_832e1db0` | `SeqExecuteLoadSaveData` | Loads slot "Index" (Master Quest adds 6). No limit of 6: slot 7 opens `slot_07` | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84e98e38` | `g_CaptionMgr` | The caption manager's holder | tested |

## Structures

### The caption manager (0xb8 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+8` | The normal array `{num, max, ptrs}` (its buffer at `+24`) | read |
| `+72` | The Master Quest array (its buffer at `+88`) | read |
| `+136` | The latest index | read |
| `+140` | The state (4: ready) | tested |
| `+142`, `+143` | Whether a save exists, and whether a Master Quest one does ([master-quest.md](master-quest.md)) | read |
| `+145` | Which array: normal or Master Quest. Set by `SeqOneShotSetMQMode` (`f_83305480`, [master-quest.md](master-quest.md)) | read |

### A caption record (0x5a0 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+4` | The slot | read |
| `+240` | The screenshot (-1: none) | tested |
| `+448 … +464` | The date | read |
| `+472` | The Autosave flag | read |
| `+473` | Clear | read |
| `+480` | The place (`{char *, size}`) | read |
| `+640` | Flags (bit 1: usable) | tested |
| `+1384 … +1392` | Medallions | read |
| `+1408` | Health | read |
| `+1412` | The maximum | read |
| `+1432` | Extra hearts | read |
| `+1434` | Master Quest | read |

## Notes

- `f_832e1db0` is `SeqExecuteLoadSaveData`'s slot 29 (OnEnter), not an evaluate: Execute nodes have no slot 39 ([sequences.md](sequences.md)). **read**
- Six is everywhere: the caption arrays, the slot chooser (`f_830506a0`), the manual save index (`f_83310630`), the latest
  index's copy (`f_833cd5d0`) and the save list's length. Loading (`f_832e1db0`) has no such limit.
- LS's side is `host/nn_fs.c`: `MountSaveData` mounts `save:` on `<game>\user\save\<user id>`, once, at boot;
  `mount()` adds or replaces a mount's folder; `host_path()` turns `save:/slot_00/progress.sav` into the file on disk, one to
  one.
