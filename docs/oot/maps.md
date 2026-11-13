# Maps

How the game loads a map (a "banc"): its entry in the map index, the layout files that place everything in it as actors,
how a layout is read and its actors are defined and created, the files named after the layout (collision, terrain,
navigation), the preloading of the next map, and the per-map tables that index the layout's actors for the map screen and
spawning. Looked at in November 2026, to learn whether Master Quest
loads other geometry ([master-quest.md](master-quest.md)).

**LS code:** none yet
**See also:** [master-quest.md](master-quest.md) (the `_MQ` maps), [events.md](events.md) (`f_829df380`, the change of
map), [game-data.md](game-data.md)

## In short

1. **The map index:** each map has a `GameBancParam` file (`Work/Banc/GameBancParam/<Map>`), gathered into the ResourceDB
   table "GameBancInfo" and looked up by the map's name. It names the map's layout (`Banc.Main`), terrain, navigation mesh,
   lighting, foliage, location and world parameters, and the neighbouring maps to preload. **read**
2. **The layout:** `Banc.Main` is a `.bcett.json` path. The loader adds "_Static" and "_Dynamic" and turns
   `Work/Banc/Scene/<Map>/…json` into `Banc/<Map>/….byml`, so a map is two files, `<Map>_Static.bcett.byml` and
   `<Map>_Dynamic.bcett.byml`. They place **everything** in the map as actors: the rooms' geometry (`Land_*`), doors,
   chests, enemies, start points, areas. **read** (the paths), **tested** (the files)
3. **The actors:** each layout entry names its archetype (`Gyaml`) and its instance (`Hash`), with a position and optional
   per-instance parameters. An archetype is `Pack/Actor/<Gyaml>.pack`: an ActorParam that lists its components (model,
   physics, AI, …), which name the model (`.bfres`), collision (`.bphsh` or a primitive) and AI (`.ainb`) files. Its
   ResourceDB "ActorInfo" row gives its class and its create and delete radii. **read**, **tested** (the packs)
4. **Files named from the layout:** the static collision (`Phive/StaticCompoundBody/<Map>/<Map>.Nin_Ounce_NVN2.bphsc`) is
   named from `Banc.Main`. Terrain, navigation mesh and volume statistics have their own names in the `GameBancParam`.
   **read**
5. **The per-map tables:** `Banc/BancBasedResource/*.byml` hold, for every map, indexes of its layout's actors (map-screen
   items, start points, areas). They are read by name; nothing in them places an actor. **tested** (the index), **read**
   (the readers looked at)

## Functions

### The map index

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8122add0` | `ResourceDB_RegisterTables` | Registers the ResourceDB's tables, among them "GameBancInfo" (`game__GameBancInfoTable`), built from `Work/Banc/GameBancParam` of type `game__banc__GameBancParam` | read |
| `f_80fcebc0` | `GameBancParam_Construct` | Sets a `GameBancParam`'s vtable, `0x84c14f48` | read |
| `f_8083b770` | `GameBancParam_VisitResolved` | Slot 19 (`+152`, at `0x84c14fe0`): hands each field to a visitor (`x1`, its vtable[0] once per field), taking a field the file doesn't set from its `$parent` chain (`+16`/`+24`). The fields: WorldAabbMin/Max, NormalizedBancName, BancUseStatic, BancUseDynamic, BancUnitSize, EnvSetName, WorldMgrName, VolumeStatsWorldName, RenderingMode, LocationalLightScenePlacement, NavMeshMgrMode, NavMeshUnitSize, NavMeshSceneName, TerrainSceneName and the other terrain fields, StaticCompoundMode, StaticCompoundUnitSize, BaseLocation, DungeonParam, FoliageScene and more | read |
| `f_81227610` | `GameBancParam_VisitRaw` | Slot 18 (`+144`, at `0x84c14fd8`): the same fields as the file has them, each with its "set" flag (from `+877`), probably for loading and saving. Neither slot has a direct caller (**guess** which runs when) | read |
| `f_82d05160` | `OptimizeChangeBancUnitParam_Parse` | Reads a preload unit (layout below): BancParamName, BancResourcePath, Priority, HasMQ, EnableGameDataHash, IsReverse, AlternativeGameData, LoadRadius, IsUseFailSafe, each with a "set" flag | read |
| `f_82d04a50` | `OptimizeChangeBancUnitParam_Parse2` | The same fields; the second of the pair | read |
| `f_82d059a0` | `OptimizeChangeBancUnitParam_Name` | The struct's name, `game__banc__OptimizeChangeBancUnitParam` | read |
| `f_82d05430` | `OptimizeChangeBancUnit_Path` | A unit's BancResourcePath, from its `$parent` file (`f_80fe3690`) when the unit doesn't set it | read |
| `f_82d05580` | `OptimizeChangeBancUnit_HasMQ` | A unit's HasMQ (`+132`), with the same fallback | read |
| `f_82d057c0` | `OptimizeChangeBancUnit_LoadRadius` | A unit's LoadRadius (`+124`), with the same fallback | read |

### Preloading the next map

A map's `OptimizeChangeBancUnits` list the neighbouring maps that load ahead, within each unit's LoadRadius, so that a
change of map is quick (the Forest Temple's list its Sage room, the Sacred Forest Meadow and Kokiri Forest). **tested** (the
file), **read**

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_810a4390` | `SeqOneShotOnChangeBancOptimizeChangeBanc_Execute` | A sequence node run on a change of map: walks the current map's preload units and adds each with `f_810a44e0` | read |
| `f_810a44e0` | `OptimizeChangeBanc_AddUnit` | Copies the unit's BancResourcePath. In Master Quest mode (`f_811f4040`), when the unit's HasMQ is set, it inserts "_MQ" before the first "." of the path, in the same folder (`…/ForestTemple/ForestTemple.bcett.json` becomes `…/ForestTemple/ForestTemple_MQ.bcett.json`), then registers the unit with `f_832baf30`. It renames only this layout path | read |
| `f_832baf30` | `OptimizeChangeBanc_Register` | Registers a unit to preload | guess |
| `f_8002f7a0` | `OptimizeChangeBancMgr_PreloadUnit` | The preload worker ("OptimizeChangeBancMgr"): for each unit it drops ".bcett.json" from the path (`f_8002eda0`), adds "_Static" or "_Dynamic" and ".bcett.json", turns it into a RomFS path (`f_80036c20`) and asks for it (`f_8002f220`); it also builds the units' actor packs' paths (`Pack/Actor/` + Gyaml + `.pack`) and models (`Model/%s.%s.bfres`) | read |
| `f_805d7660` | `BancEntity_ParseActorForPreload` | The preload's twin of `f_83b12ea0` (below) | read |

### Loading the layout

The map's layout is loaded by the BancLoadMgr (named by `f_809de840`, vtable `0x84bc0a40`). **read**

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_809de840` | `BancLoadMgr_Name` | The class's name, "BancLoadMgr" | read |
| `f_809d6bf0` | `BancLoadMgr_LoadStatic` | Asks for the Static layout: adds "_Static" (`0x83d2037d`) when BancUseStatic is set (`f_809d72f0`). For a map split into units it names unit files `%c-%c` (A-1, B-2 …) from a grid at `+456`/`+460` and their "_DistanceView" files, and it can ask for a "_DebugIdTable". The game uses none of these: each map has only its two layout files | read |
| `f_809d8150` | `BancLoadMgr_LoadDynamic` | Slot 12 of the vtable: asks for the Dynamic layout, adding "_Dynamic" (`0x83cc9bd9`) when BancUseDynamic is set | read |
| `f_809d8780` | `BancPath_AddSuffix` | Puts a suffix before a file name's extension (it looks for the "." and "/") | read |
| `f_809d72f0` | `GameBancParam_UseStatic` | BancUseStatic (`+833`, its "set" flag bit 0 of `+878`) | read |
| `f_809d80a0` | `BancLoadMgr_Request` | Passes a layout path on, through `f_809d7d10` to `f_83b13110` | read |
| `f_809d7d10` | `BancLoadMgr_Request2` | The next step of that chain | read |
| `f_83b13110` | `BancEntity_Load` | Turns the path into a RomFS path (`f_80036c20`) and asks for it as a "BancEntity" resource (`f_8002f220`) | read |
| `f_80036c20` | `BancPath_ToRomPath` | A path that starts with "Work/Banc/Scene/" and ends in ".json" becomes "Banc/" + the middle + ".byml" (`Work/Banc/Scene/ForestTemple/ForestTemple_Static.bcett.json` → `Banc/ForestTemple/ForestTemple_Static.bcett.byml`); any other path goes to `Banc/%s.byml` (`0x83caff15`) through `f_80c762d0` | read |
| `f_8002f220` | `Resource_Request` | Asks the resource system for a file by path and type | guess |
| `f_83298000` | `BancUnit_FileName` | Names a map unit's file (`%c-%c`) | read |

### Reading the layout

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80ed6a60` | `BancModule_Install` | Makes the "BancModule" thread (`0x83e0a68b`): a sead::DelegateThread (`f_8069db80` → `f_8069dbe0`: `CreateThread` and a light message queue) with the delegate `f_80c1ac20`, priority 24. Not a job ([jobs.md](jobs.md)); Porter, NfpMgr, EngineUILowPriorityWorker, gmd::SaveDataMgr, the EventMgr loaders and the movie threads are made the same way | read |
| `f_80c1ac20` | `BancModule_Run` | Walks the loaded layout slots (0x2f0 bytes each, at `+344`, their count at `+336`) and reads each with the two functions below | read |
| `f_80c1ba50` | `BancLayout_Parse` | Reads the layout's root: FilePath, AiGroups and Actors, each actor with `f_83b12ea0`, then each actor's Links and Rails. Its arguments are the slot, the actor registry (`[0x84eb1940]`) and the PlacementMgr (`[0x84eb1930]`). Links join actors by Hash: a first pass builds a table sorted by Hash (`slot+680`, its count at `+696`), a second looks each link's Dst up in it | read |
| `f_80c1b7d0` | `BancLayout_ParsePhiveHeader` | Reads PhiveHeader's PlacementGroups | read |
| `f_83b12ea0` | `BancEntity_ParseActor` | Reads one layout entry into an entity (layout below). Gyaml and Hash are required, the rest optional. It doesn't read Presence | read |
| `f_806876c0` | `BancModule_FarDeleteGroups` | Slot 10 of the BancModule's second vtable (`0x84c7dbc0`): reads FarDeleteGroups, lists of actor Hashes | read |
| `f_80688120` | `BancModule_SimultaneousGroups` | Slot 9 of that vtable: reads SimultaneousGroups, lists of actor Hashes | read |
| `f_81145b00` | `BancLayout_ParsePlgGdTable` | Reads PlgGdTable: each PlgGuid to a GameDataType (Int, Bool, BoolExp) and GameDataPath | read |
| `f_80cc0160` | `BancLayout_ParseRails` | Reads Rails | read |

### Defining and creating actors

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83b072b0` | `ActorRegistry_AddInstance(registry, unit, &name, …)` | Adds a layout entry to the actor registry (`[0x84eb1940]`): finds its archetype by name (`f_802dbd90`), skips archetypes that are not valid (`+84`) or have bit 3 of `+190`, and links a pooled instance node | read |
| `f_802dbd90` | `ActorRegistry_Find` | Finds an archetype's entry (0xd8 bytes; the table at `+160`/`+168`) by its name's hash (`f_801939e0`) | read |
| `f_802d5480` | `ActorName_FromPath` | The base name of a "Work/…" path, for a Gyaml given as a path (no layout in this game does that) | read |
| `f_82ddfad0` | `ActorPack_Path` | Builds `Pack/Actor/%s.pack` (`0x83cfa29f`) and `Actor/%s` for an archetype | read |
| `f_83afa980` | `ActorParam_Load` | Looks up `Actor/<name>` of type `engine__actor__ActorParam` (through `f_801a9540`) and reads ActorNameRef, SystemSetting, UsePreActorRenderer and IsFarActor | read |
| `f_805cb760` | `ActorInfoTable_ParseRow` | A row of the ResourceDB's ActorInfo table (`engine__rsdb__ActorInfoTable`): ActorName, ClassName, ModelProjectName, FmdbName, the animation resources, CreateRadiusOffset, DeleteRadiusOffset, LoadRadius, UnloadRadiusOffset, CalcRadius, DisplayRadius, IsFarActor, InstanceHeapSize and more | read |
| `f_80d7aa80` | `PlacementMgr_Create` | Makes the PlacementMgr (`0x84eb1930`). It probably creates and deletes actors by distance, with the ActorInfo radii (**guess**) | read |
| `f_80d7ac00` | `PlacementMgr_MakeHeap` | Makes its "PlacementMgr" heap | read |

### Geometry, collision and navigation
### Geometry, collision and navigation

None of these reads Master Quest mode, HasMQ or "_MQ". **read**

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80844740` | `StaticCompound_PathFromBanc` | The static collision's path: strips "Work/Banc/Scene" and "/" from `Banc.Main`, cuts at the ".", and formats `Phive/StaticCompoundBody/<Map>/<Map>.Nin_Ounce_NVN2.bphsc`. Called from the sequence node `SeqOneShotInitializeStaticCompoundBodyMgr` | read |
| `f_80715ae0` | `StaticCompound_Load` | Another static collision loader on the same path | read |
| `f_832cc540` | `StaticCompound_Load2` | Another static collision loader | read |
| `f_809d6110` | `StaticCompound_Load3` | Another static collision loader | read |
| `f_80a06fa0` | `Terrain_Load` | Builds the terrain's path, `%s/%s.tscb`, from the `GameBancParam`'s terrain fields | read |
| `f_80b0c4e0` | `Terrain_Load2` | The same | read |
| `f_832f4230` | `NavMesh_Load` | The navigation mesh, under `Phive/NavMesh` | read |
| `f_82e13ac0` | `NavMesh_Load2` | The same | read |
| `f_80718040` | `VolumeStats_Load` | The volume statistics, under `Phive/VolumeStats` | read |
| `f_832c3480` | `VolumeStats_Load2` | The same | read |

### The per-map tables (BancBasedResource)

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80e55520` | `BancBasedResourceMgr_Create` | Makes the manager (0x220 bytes, layout below); its holder is `0x84e49448` | read |
| `f_80c63720` | `BancBasedResourceMgr_Construct` | Sets the string at `+0` to "" | read |
| `f_80e55600` | `BancBasedResourceMgr_RequestLoad` | Starts the load | read |
| `f_80e55d20` | `BancBasedResourceMgr_Load` | Loads the 7 tables, `Banc/BancBasedResource/%s%s%s.byml` (`0x83e21d01`), from the table's name, ".120" or nothing, and the string at `+0`: AlternativeAnchor, GmdReset, LocationArea, MapParam, NoEscapeRangeArea, StartPos, StaticGameDataArea. It asks for the ".120" files when add-on 1 is owned and the release version is at least 120 ([master-quest.md](master-quest.md)), and then stores 1 at `+404` | read |
| `f_8113b7a0` | `DungeonMap_Load` | The dungeon map screen: reads Master Quest mode and calls `f_8113b9d0` | read |
| `f_8113b9d0` | `DungeonMap_ParseMapParam` | Reads a map's MapParam entry: BossRoomId, FloorNum, MinFloor, MapMode, Floor, TransY, Door (Trans, Rotate, IsChainLocked, IsBossRoom), TBox, InstanceID, Room, CenterTrans, IsInvisibleMiniMap, AreaAnchor | read |
| `f_805ccb20` | `MapParam_FloorAt` | Works out the floor from FloorNum, MinFloor, Room and TransY | read |
| `f_805aa0d0` | `MapParam_NormalizeName` | The map's name as MapParam keys it (`%s_Adult`, `%s_Child`, and special cases such as HyruleField and LonLonRanch) | read |
| `f_829e1170` | `QueryBancStartPosByName_Evaluate` | A sequence query: a map and a start point's name give its Position and Rot from StartPos. Its name is `f_829e14f0` ("QueryBancStartPosByName") | read |
| `f_832a57b0` | `StartPos_Spawn` | Reads "StartPos" and "StartRot", the spawn point (**guess**), and after the StartPos lookup looks the current map (`+416`) and the start point's name up in NoEscapeRangeArea, keeping the entry at its `+496` | read |
| `f_82d065d0` | `NoEscapeRangeArea_Check` | Calls `f_82d062d0` | read |
| `f_82d062d0` | `NoEscapeRangeArea_Contains` | Tests a position against an entry's Shape | read |
| `f_8114b3b0` | `BancBasedResourceMgr_SetMap` | Updates the manager's `+400`, `+408` and the current map's name (`+416`) | read |
| `f_82d061d0` | `BancBasedResourceMgr_Dispose` | The manager's disposer | read |
| `f_83050450` | `StartPos_LookUp` | Looks a StartPos entry up by the current map's name | read |
| `f_83322490` | `StartPos_LookUp2` | The same | read |
| `f_802d1520` | `AlternativeAnchor_Read` | AlternativeAnchor for the NPCs' schedules: TargetRestPointInstanceName, AnchorAction1 … 10, AnchorSleep, AnchorGoBackHome | read |
| `f_80413540` | `AlternativeAnchor_Read2` | The same | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84e49448` | `g_BancBasedResourceMgr` | The per-map tables' manager (its GOT slot is `0x84ca10a8`) | read |
| `0x83e21d01` | `BancBasedResourcePathFormat` | `Banc/BancBasedResource/%s%s%s.byml`: the table's name, ".120" or nothing, and the manager's suffix | read |
| `0x84c14f48` | `GameBancParam_vtable` | A `GameBancParam`'s vtable; slots 18 and 19 are its two field visitors | read |
| `0x84eb1940` | `g_ActorRegistry` | The registry of archetypes and their placed instances | read |
| `0x84eb1930` | `g_PlacementMgr` | The PlacementMgr | read |
| `0x84bc0a40` | `BancLoadMgr_vtable` | BancLoadMgr's vtable; slot 12 is `f_809d8150` | read |
| `0x84c7dbc0` | `BancModule_vtable2` | The BancModule's second vtable; slots 9 and 10 read SimultaneousGroups and FarDeleteGroups | read |
| `0x83caff15` | `BancRomPathFormat` | `Banc/%s.byml` | read |
| `0x83cfa29f` | `ActorPackPathFormat` | `Pack/Actor/%s.pack` | read |
| `0x83d2037d` | `str_Static` | "_Static" | read |
| `0x83cc9bd9` | `str_Dynamic` | "_Dynamic" | read |
| `0x83e0a68b` | `str_BancModule` | "BancModule" | read |

## Structures

### BancBasedResourceMgr (0x220 bytes)

Each table has a 56-byte slot; the offsets below are each table's data root.

| Offset | Field | Sure |
|---|---|---|
| `+0` | A suffix for every table's path, before ".byml" (the format's third argument): "" (`0x83cfdd43`) from the constructor, and nothing found changes it | read |
| `+48` | StartPos | read |
| `+104` | LocationArea | read |
| `+160` | StaticGameDataArea | read |
| `+216` | AlternativeAnchor | read |
| `+272` | NoEscapeRangeArea | read |
| `+328` | MapParam | read |
| `+384` | GmdReset | read |
| `+400`, `+408` | Updated with the map's name by `f_8114b3b0` | read |
| `+404` | 1 when the ".120" tables were asked for | read |
| `+416` | The current map's name | read |

### A layout entry, as read (`f_83b12ea0`)

| Offset | Field | Sure |
|---|---|---|
| `+0` | Translate | read |
| `+12` | Rotate | read |
| `+24` | Scale | read |
| `+40` | Gyaml, the archetype | read |
| `+48` | Name | read |
| `+56` | Hash, the instance | read |
| `+64` | SRTHash | read |
| `+72` | Phive.Placement.GroupGUID | read |
| `+80` | The Dynamic node: per-instance parameters | read |
| `+96` | The Phive node | read |
| `+112` | Bit 0: Bakeable (no reader found) | read |
| `+116` | 1 when Gyaml does not start with "Work/" | read |
| `+117` | 1 when the entry was read | read |

### GameBancParam (fields found so far)

| Offset | Field | Sure |
|---|---|---|
| `+680` | BancUnitSize | read |
| `+832` | BancUseDynamic (its "set" flag: `+879`) | read |
| `+833` | BancUseStatic (its "set" flag: bit 0 of `+878`) | read |
| `+842` | A byte that `f_809d76a0` copies into `Sequence_IsChildhood` (its "set" flag: `+929`) | read |

No map in the game sets BancUnitSize, BancUseStatic or BancUseDynamic in its file, so they keep the class's defaults (not
found). **tested** (the files)

### OptimizeChangeBancUnitParam

Each field has a "set" flag at `+136` … `+144`; a field not set falls back to the `$parent` file.

| Offset | Field | Sure |
|---|---|---|
| `+56` | AlternativeGameData | read |
| `+64` | BancParamName | read |
| `+72` | BancResourcePath | read |
| `+120` | EnableGameDataHash | read |
| `+124` | LoadRadius | read |
| `+128` | Priority | read |
| `+132` | HasMQ | read |
| `+133` | IsReverse | read |
| `+134` | IsUseFailSafe | guess |

## Notes

### What a map is made of

From the Forest Temple's files, decompressed (the layouts with the dictionary `bcett.byml.zsdic` in `Pack/ZsDic.pack.zs`)
and parsed as BYML. **tested**

- **The `GameBancParam`** names, besides the fields above: `Banc.Main` (the layout), `BaseLocation`
  (`Work/Location/<Map>…gyml`), `DungeonParam`, `FoliageScene`, `HeightMapParam`, `LocationalLightScenePlacement`,
  `WorldMgrName`, `OptimizeChangeBancUnits` and the scene's lighting, fog and sound settings. Nothing in it is about Master
  Quest except the units' HasMQ.
- **The layouts:** Static holds `Actors`, `AiGroups`, `FarDeleteGroups`, `FilePath`, `PhiveHeader`, `PlgGdTable`, `Rails`
  and `SimultaneousGroups`; Dynamic holds `Actors` and `FilePath`. Every actor has `Gyaml` (its archetype), `Hash` (its
  instance ID), `Translate`, `Phive`, `SRTHash` and `Bakeable`. The Forest Temple's Static places 906 actors and its Dynamic
  2,307.
- **Geometry is actors:** the rooms, walls and props are `Land_*` actors (120 kinds in the Forest Temple, such as
  `Land_ForestTemple_Room_A_09` and `Land_ForestTemple_WallRoom25_D`), each with its `Pack/Actor/<name>.pack`. Most of the
  Dynamic layout is `Land_*`. So which geometry a map shows is decided by its layout, not by a separate scene file.

### What an actor is made of

From the 271 archetypes the Forest Temple's layouts place, their packs decompressed (dictionary `pack.zsdic`) and read.
**tested**

- **A pack is a SARC of BYML (`.bgyml`) and `.ainb` files only.** Models (`.bfres`), collision meshes (`.bphsh`) and
  animation (`.asb`) are RomFS files of their own that the pack's files name.
- **Every pack has `Actor/<name>.engine__actor__ActorParam.bgyml`**, whose `Components` is a dict of references
  (`ModelInfoRef`, `PhysicsRef`, `AIInfoRef`, `ASRef`, `BlackboardRef`, `GameParameterTableRef`, `ELinkRef`, `SLinkRef`,
  `XLinkRef`, `DropRef`, `LODRef`, `PreActorSettingRef`, `SystemSetting`, `ActorLogicNodeRef` and others), with an optional
  `$parent`. A pack carries its parent's ActorParam too, and a child sets only what differs (`Obj_ForestTemple_Door_A_01`
  inherits from `Obj_Base_Door_A_02` and sets its model, physics and links).
- **The references:** a component reference starting with `?` names `<Dir>/<Name>.<type>.bgyml` in the pack; a `Work/…gyml`
  path names the `.bgyml` of that name; an AI reference `Work/AI/Root/X.root.ain` names `AI/X.root.ainb`. Some (the
  defaults, such as `Component/LODParam/Default`) are not in the pack: they are in the resource size table, probably in a
  resident pack.
- **ModelInfo** gives `ModelProjectName` and `FmdbName`: the model is `Model/<ModelProjectName>.<FmdbName>.bfres`.
- **Physics:** `PhysicsRef` names a `PhysicsParam`, whose `ControllerSetPath` names a `ControllerSetParam`. Its shapes are
  primitives (`Box`, `Capsule`, `Sphere`, `Cylinder`, `Polytope`, `Prism`, `Cone`) or `PhshMesh` paths to
  `Phive/Shape/Dcc/<n>.Nin_Ounce_NVN2.bphsh`.
- **Room geometry (`Land_*`, 120 kinds in the Forest Temple)** is a minimal actor: an ActorParam (7 references), a
  ModelInfo and a static physics body. 85 of them share the model project `Land_ForestTemple_Room_A`, one `.bfres` per
  piece (`Model/Land_ForestTemple_Room_A.Land_ForestTemple_Room_A_09.bfres`). Their collision is per piece: meshes named
  `<piece>__Physical`, `__StairSlope`, `__StairStep` and `__Airwall`, or a primitive `Box` (the wall pieces), or a shared one
  (ivy, leaves and webs).
- **Others:** a chest (`Obj_TBox_A_01`) has about 50 files (AI, attention, animation, drops, a full physics set); the Gold
  Skulltula about 100 (16 `.ainb`, life, damage, game balance); a door its parent's ActorParam, the AI
  `Obj_DungeonDoor.root.ainb` and its game parameters; `StartPos` and `DestinationAnchor` an empty model and a dummy body;
  `DungeonRoomArea` a box sensor, AI and a blackboard (`DungeonRoomID`).
- **No variant in the packs:** no file, key or string in them is about Master Quest; no parameter swaps a model by mode.
  Master Quest's only switch at this level is the map-index units' HasMQ ([master-quest.md](master-quest.md)).

### The two layouts

The Forest Temple's, parsed. **tested**

- **Keys:** every actor has Gyaml, Hash, Translate, Phive (`Placement.ID`, sometimes a group) and SRTHash. Static's 906 also
  have Rotate (378), Dynamic (324), Scale (308), Name (252), Links (134), Bakeable (91), Presence (9: `FlagName`, `IsCheckLazy`,
  `IsNegation`; **guess**: a game-data flag that decides whether the actor is there) and Rails (6). Dynamic's 2,307 have Bakeable (2,075), Rotate (1,666), Dynamic (45), Scale (29)
  and Name (27), and no Links, Presence or Rails.
- **Dynamic** (the node) holds per-instance parameters, about 75 names, the commonest `DefaultDungeonRoomIndex`.
  **Links** are `{Src, Dst, Gyaml, Name}` (462 in Static, mostly of kind `Reference`).
- **Static's other keys:** `AiGroups` (150: a logic module, `Logic/Module/…logic.module.ain`, with the actors it
  references), `FarDeleteGroups` (116) and `SimultaneousGroups` (114), lists of actor Hashes, `PhiveHeader` (2 placement
  groups that move), `PlgGdTable` (2 game-data flags, such as a room's cleared flag) and `Rails` (4 paths).
- **What goes where:** no actor is in both. Dynamic is 2,212 `Land_*` pieces of its 2,307 plus some pots, enemies and
  anchors that nothing refers to. Static holds every actor that links, groups, logic modules or flags refer to (all
  links and groups point at Static actors), with 24 `Land_*` pieces. So the rule is probably that an actor other parts must
  find by Hash goes in Static, and plain scenery in Dynamic (**guess**: 103 Static actors are referred to by nothing).
- **Bakeable** is set on most `Land_*` pieces (2,075 Dynamic, 91 Static). It likely marks what is baked (lighting, or
  the static collision) (**guess**: nothing found reads it).

### Variant maps: age, Alternative, Master Quest

A map's variants are separate maps, each with its own `GameBancParam` and layouts, chosen by name when the map is entered
(`f_829df380`, [events.md](events.md)): **tested** (the files), **read** (the naming)

- **Age:** `_Child` and `_Adult` (16 and 19 of the 80 maps), picked by `Sequence_IsChildhood`.
- **Alternative:** two maps, both adult:
  - `KakarikoVillage_Adult_Alternative`, the burned village: it drops some intact houses and adds
    `Land_Kakariko_BurnedParts_*`, more building parts and `EftLoc_KakarikoVillage_BurnedSmoke_00`. Its game-data bool is
    `Sequence_IsAlternative_KakarikoAdult`.
  - `ZoraDomain_Adult_Alternative`, Zora's Domain thawed: the normal adult map is the frozen one
    (`EftLoc_ZoraDomain_ForzenZora_00`, the `Land_Zora_CliffAdult_*` cliffs), and the alternative has water planes
    (`Land_ShallowWaterPlane_*`), plants, a carpet and the `Land_Zora_Cliff_A_*`/`_B_*` cliffs and pillars. Its bool is
    `Sequence_IsAlternative_ZoraDomainAdult`.

  Each inherits its `GameBancParam` from the normal adult map (`$parent`) and sets its own `Banc` (layout), FoliageScene,
  HeightMapParam, bake settings, sound scenery and preload units (Zora's Domain also its lighting, shadows and world
  parameters). Its layouts are separate files that share almost no actors with the normal ones (9 of about 2,200
  instance Hashes in Kakariko, 8 of 2,400–3,100 in Zora's Domain). The maps that preload Kakariko or Zora's Domain name
  the bool in their units' AlternativeGameData (Bottom of the Well, Death Mountain Trail, the Graveyard, Hyrule Field, Zora's
  Fountain, Zora's River).
- **Master Quest:** `_MQ`, [master-quest.md](master-quest.md); none is in this dump.
- **No mirrored maps:** no map or file name is about mirroring. The image's "Mirror" strings are the Mirror Shield, the Spirit
  Temple's mirrors, Dark Link and audio, plus three parameter sets: `MirrorX`/`MirrorZ` with Dir, PosOffset and
  UseDynamicOffset (an offset's parameters, `f_8386f010`, `f_8386f420`), `IsUseMirroring`/`AllowTransToMirroring` with an
  animation's play rate and prediction settings (`f_839ee620`, `f_839eeee0`), and `EnableMirrorMotionLR` with arm bones
  (`f_838517c0`, `f_83851c70`). None is a map's. **read** (the strings), **tested** (the files)

### The per-map tables are indexes of the layout

Every map-screen item in the Forest Temple's MapParam entry (43 doors, chests, Gold Skulltulas and facilities) has an
`InstanceID` equal to the `Hash` of an actor in its Static or Dynamic layout, 42 of them at the same position, and so do all
71 of its rooms' entrances. The doors are `Obj_ForestTemple_Door_A_01` and switch shutters, the chests `Obj_TBox_*`, the
markers (`CollectSign`) `Enemy_Skulltula_Gold`. The tables are baked from the layout: they record where things are and
what they are for the map screen and spawning; the layout places them. **tested**

The readers looked at (map screen, StartPos, NoEscapeRangeArea, AlternativeAnchor) look entries up by the map's name and
read positions, IDs, shapes and floors; none creates an actor. **read** (those), **guess** (LocationArea, GmdReset and
StaticGameDataArea, whose readers were not found: no direct access to their slots turned up)

## Open questions

- The readers of LocationArea, GmdReset and StaticGameDataArea (`f_82953f30`, which uses a Location `.gyml`, and
  `f_80414460` are candidates).
- When each `GameBancParam` visitor runs (no direct callers).
- The class factory: how an archetype's ActorInfo ClassName becomes a C++ class, and where the PlacementMgr turns registered
  instances into live actors.
- Who reads Bakeable (`+112`), Presence and Phive.Placement.ID, and what FarDeleteGroups and SimultaneousGroups do at run
  time.
- Where the shared component files (defaults such as `Component/LODParam/Default`, in the resource size table but not in
  the packs) live, and `PreActorSetting`, `ActorName` and `GameLifeCondition`, which aren't even listed.
- The defaults of BancUnitSize, BancUseStatic and BancUseDynamic, and what fills the loader's base path (`f_809d6bf0` calls
  `f_80844ce0` for `Banc.Main` but seems not to use the result).
