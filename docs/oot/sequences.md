# Sequences and script nodes

The game's script graphs: the classes named `Seq*`, `Query*`, `OneShot*`, `Execute*`, `Selector*`, `Hold*`, `Trigger*`
and `Operate*` that the `.ainb` files in the RomFS wire together. The title, the new-game flow, changes of map and the
menus run as Sequence graphs (`Sequence/*.module.ainb`, from `FormalSequence.root.ainb`); the actors run AI and Logic
graphs. This page covers how a node class is registered and found, how a node is made and bound to its parameters, the
shape every node's vtable has, how a node is run, and a catalogue of the 5,716 registered classes. It was looked at in
November 2026 by static analysis of the binary and of the loose `.ainb` files' node tables.

**LS code:** `host/hos_main.c` (the debug tools: it calls `OneShotAddOrRemovePouchContent`'s
calc on a node it builds on the stack, and the functions behind `QueryGetPouchContentNumByActorName`, `ExecuteWmSetTime`,
`SeqOneShotRequestEvent` and `OneShotPhysicsCctChangePosition`; see [LS](#ls))
**See also:** [master-quest.md](master-quest.md) (the title's nodes, `SeqQueryHasAoCMQ`), [events.md](events.md) (the
event request), [save-data.md](save-data.md), [save-list.md](save-list.md), [game-data.md](game-data.md) (the hashes),
[player.md](player.md) (the pouch nodes), [maps.md](maps.md), [culling.md](culling.md), [textures.md](textures.md),
[audio.md](audio.md), [frame-time.md](frame-time.md) (the frame-time keeper), [actors.md](actors.md), [jobs.md](jobs.md)

## In short

1. **A class registers itself at boot.** Each class has a static initialiser (5,574 of them in the init array,
   `0x848709e8` to `0x8487f368`) that fills a 32-byte entry with a key and its create function and pushes it on one list
   (`f_80ca4a00`, `f_80ca4f70`). **read**
2. **The key is murmur3(class name) + murmur3(category).** The category is the string the class's slot 20 returns:
   `AI`, `Sequence`, `Logic`, `CommonSequence` or `CommonNode`. This holds for 5,621 of the 5,716 entries (for
   SeqQueryHasAoCMQ: 0xb227a052 + 0xe5937b14 = 0x97bb1b66). The other 95 are compiled graph modules, keyed by murmur3 of
   a `<Name>.module` string plus the category (31 matched from the class name, such as `DisplayOnlyShake.module`).
   murmur3 is x86 32-bit with seed 0, the same as the game data's ([game-data.md](game-data.md)). **read** (the sum
   checked for every entry)
3. **The `.ainb` stores murmur3 of each node's name** in the node's record (`+12`; true for all 23,953 named nodes in
   the 1,942 loose files). To make a node, the game adds each of the graph's category hashes in turn and looks the sum up
   in a hash table built from the list (`0x84ea5350`). A Sequence graph tries `AI`, `Sequence`, `CommonSequence`,
   `CommonNode`, in that order. **read**
4. **Every node's vtable has the same frame.** Slot 19 is the name, 20 the category, 28 declares the parameters,
   29/30/31 are OnEnter/OnUpdate/OnLeave, and 39 (40 for Logic) is the calc. Queries and one-shots call slot 39 from
   OnEnter; a query calls it again each Update only when bit 1 of `+24` is set. Execute nodes have no slot 39: they work
   in OnEnter and OnUpdate. The result is in bits 30 and 31 of `+24`: 2 done, 3 (**guess**: failed). **read**
5. **Waits count 60 Hz frames.** `SeqExecuteGenericWaitFrame` adds the frame-time keeper's step to its counter each
   Update, so it waits the same time at any frame rate as long as the step is right ([frame-time.md](frame-time.md)).
   **read**

## Functions

### Registering a class

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80ca4a00` | `NodeRegistry_Get` | Returns the node registry's list (`[0x84c9da38]`), making it on first use (guard `[0x84c9da40]`): empty, count 0, and the link's offset in an entry (`+20`) set to 16. Every static initialiser calls it | read |
| `f_80ca4f70` | `NodeRegistry_Link(list, link)` | Pushes a link on the front of a list: the link's `+0` gets the old first link, its `+8` the list, and the old first link's `+8` the new one. The initialiser then adds 1 to the list's count (`+16`) | read |
| `f_8108d7c0` | `SeqQueryHasAoCMQ_Register` | SeqQueryHasAoCMQ's static initialiser ([master-quest.md](master-quest.md)): stores the key 0x97bb1b66 at `0x84e92f98+0`, the create function `f_8331a6c0` at `+8`, zeroes `+16` and `+24`, and links the entry. The other initialisers are the same 24 instructions with their own key, entry and function | read |
| `f_80ca1c30` | `NodeRegistry_InitCommon` | The one initialiser that registers many classes (143): the 102 CommonSequence classes, 39 shared AI ones (`DrawSphere`, `QueryMathAddF32` and the other maths queries) and the two CommonNode ones (`QueryF32OutputBoundValue`, `QueryMathF32Min`) | read |
| `f_801939e0` | `Murmur3_String(text, seed)` | murmur3 (x86, 32-bit) of a C string, read up to its 0. The keys computed at run time, the category hashes and many of the game's name hashes come from it ([maps.md](maps.md), [events.md](events.md) use it) | read |
| `f_80fd6440` | `LookIKControllerArbiterRequestPause_Register` | An attachment class's initialiser, which computes its key at run time: `Murmur3_String("AI") + Murmur3_String("LookIKControllerArbiterRequestPause")`, stored in the entry `0x84df1868` with the create function `f_80ced6a0`, linked into the second registry. Eleven classes register this way (`HorseSlipBehavior`, `CameraIndoor`, `CameraDoorOpenMove`, `ListenerChangePresetAttachment`, …) | read |
| `f_80fd67d0` | `AttachmentRegistry_Get` | The second registry's list (`[0x84c9dca8]`, guard `[0x84c9dcb0]`), the same shape as the node registry. **guess** that it holds the `.ainb` attachments, from its classes' names | read |

### The factory tables

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80fd5f10` | `AIModule_BuildFactories(module, &heap)` | Builds both tables from the registries (`f_80fd60c0`, then `f_80fd6280`), clears the u16 at `module+60` and makes an 8-byte object (vtable `0x84c46b20`) at `module+8`. Called once, from `f_80fd5d00` | read |
| `f_80fd60c0` | `NodeFactory_Build(heap)` | Allocates the table at `0x84ea5350` with twice as many slots as the registry has entries (`f_80fd61d0`), then takes each entry off the list and puts its key and create function in the first free slot from key mod capacity (linear probing). A key already in the table is skipped, and so is a key of 0 | read |
| `f_80fd61d0` | `NodeFactory_Alloc(capacity, heap)` | The table's two arrays (u32 keys, then 8-byte create functions), the keys zeroed; stores them, the count 0 and the capacity at `0x84ea5350` | read |
| `f_80fd6280` | `AttachmentFactory_Build(heap)` | The same for the second registry, into the table at `0x84ea5138` | read |
| `f_80fd5d00` | `AIModule_vt9` | Slot 9 of the vtable `0x84c7a7c8`: a module's set-up that uses the name "AIModule" and builds the factory tables (`f_80fd5f10`, at 0x80fd5e04). **guess** that the vtable is `engine::ai::AIModule`'s | read |
| `f_80fd0330` | `AIModule_LoadDefinitions` | Slot 7 of the same vtable: reads the node definitions and file lists by name, `AI/NodeDefinition/Node.%s.aidefn.byml`, `Logic/NodeDefinition/Node.%s.aidefn.byml` and `%s/FileEntry/FileEntry.%s.byml` (the RomFS has `Node.Product.111.aidefn.byml.zs` and `FileEntry.Product.111.byml.zs`), with the unversioned `Node.aidefn.byml` names as well | guess |

### Making a node

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80198f50` | `AIFile_SetCategories(?, ctx, file)` | Fills a context's category list by the file's kind (`file+0x2848`): the names at `ctx+104` and their murmur3 at `ctx+232`, the count at `ctx+296` (16 inline). Kind 0: `AI`, `CommonAI`, `CommonNode`. Kind 1: `Logic`, `CommonLogic`, `CommonNode`. Kind 2: `AI`, `Sequence`, `CommonSequence`, `CommonNode`, and the context is named "Sequence". Kind 3: `AI`, `CommonAI`, `CommonNode`, named "MetaAI". No class registers under `CommonAI` or `CommonLogic` | read |
| `f_8005aba0` | `AIGraph_CreateNodes` | Makes a graph's nodes: calls `f_8005b560` (at 0x8005af24, 0x8005af80 and 0x8005b4bc) and, for attachments, `f_80a53800` (0x8005b3ac). Reached from `f_83ad8e90` through `f_800582d0` and `f_8005a950` | guess |
| `f_8005b560` | `AINode_Create(…, record, …, categories)` | Makes one node from its record in the file (`x3`). A record with no name is a built-in element (`Element_Sequential`, `Element_Simultaneous`, `Element_S32Selector`, `Element_BoolSelector`, `Element_ModuleIF_Input_Bool`, `Element_Expression`, …), made by `f_8005cbf0` by its type (`record+0`). A named one is first looked up by murmur3 of its name in a tree of the graph's own (entries tagged 0xd702d6b0; **guess**: the module and actor-logic nodes); otherwise, for each category hash in the list (`x6+128`, count `x6+192`), the key `record+12` + category is looked up in `0x84ea5350` and the first hit's create function makes the node. Then it stores the graph at `+8`, the record's index (`record+2`) at `+28`, and the flags at `+24` | read |
| `f_8005cbf0` | `AINode_CreateBuiltin` | Makes a built-in element node by its type; called from `f_8005b560` for a nameless record | guess |
| `f_80a53800` | `AIAttachment_Create` | The same category-list lookup, in the attachment table `0x84ea5138` | read |
| `f_83ad8e90` | `AIControllerUnit_Create` | Makes an "AIControllerUnit" for an `.ainb` (it uses ".ainb", "ainb" and "Unknown") and builds its graph (`f_800582d0`). Called from seven places, the Sequence component among them | guess |
| `f_83bdd800` | `SequenceComponent_CreateController` | Slot 18 of the vtable `0x84c92ad8`, a component class whose slot 8 names it "Sequence" (and "Icon=PLAYER"): makes the sequence's AIControllerUnit (with "Work") through `f_83ad8e90` | guess |

### The vtable's shared functions

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_806b7eb0` | `Node_Setup` | Slot 5: calls slot 28 (declare and bind the parameters), then slot 33 | read |
| `f_837cbb30` | `Node_Enter` | Slot 7: clears the node's bit in its graph's bit set (`[+8]+136`, by `+28`; the bit count is `[+8]+128`) and jumps to slot 29 | read |
| `f_837cbb70` | `Node_Update` | Slot 8: jumps to slot 30 | read |
| `f_837cbb80` | `Node_Leave` | Slot 9: calls slot 31 unless it is one of the empty defaults, then sets the node's bit | read |
| `f_8054dd00` | `Node_GetParams` | Slot 21: the node's parameter block in the file. The file holder is `[[+8]+16]`: its `+16` is the file's base and its `+72` the node records (60 bytes each). The result is the base plus `+20` of record number `+28`. The binders inline it when a class keeps this slot | read |
| `f_80c33cd0` | `Node_Destroy` | Slot 2 for most classes: puts back the base vtable (`0x84c47ae8`) and, when `+16` is set, calls through what it points to | read |
| `f_808aaa10` | `SeqQuery_OnEnter` | Slot 29 of the Sequence queries: calls slot 39 with a copy of its argument (8 bytes and a float) | read |
| `f_800b1c60` | `SeqQuery_OnUpdate` | Slot 30 of the Sequence queries: calls slot 39 again only when bit 1 of `+24` is set | read |
| `f_832e9ae0` | `SeqOneShot_OnEnter` | Slot 29 of the Sequence one-shots: calls slot 39 once. Their slot 30 is `f_82638ed0`, which does nothing | read |
| `f_83bd2000` | `CommonSeqOneShot_OnEnter` | The same for the CommonSequence one-shots | read |
| `f_83bd8bf0` | `CommonSeqQuery_OnEnter` | The same as `f_808aaa10` for the CommonSequence queries; their OnUpdate (`f_83bd8c30`) also tests bit 1 of `+24` | read |
| `f_83adc560` | `AIOneShot_OnEnter` | Slot 29 of the AI one-shots: calls slot 39, then sets bits of the byte at `+31` from the flags at `+24` | read |
| `f_83adc610` | `AIOneShot_OnUpdate` | Slot 30 of the AI one-shots: calls slot 39 when bit 1 of `+24` is set | read |
| `f_83adc630` | `AIQuery_OnEnter` | Slot 29 of the AI queries: calls slot 39, then marks the node done (status 2) unless bit 20 of `+24` is set | read |
| `f_83adc6c0` | `AIQuery_OnUpdate` | Slot 30 of the AI queries: calls slot 39 when bit 1 of `+24` is set | read |
| `f_8320b900` | `LogicNode_OnEnter` | Slot 29 of the Logic nodes (Hold, Trigger, Operate): calls slot 39 with the float at its argument's `+8` | read |
| `f_8320b940` | `LogicNode_OnUpdate` | Slot 30 of the Logic nodes: calls slot 40 with the same float. A Hold node's slot 39 is empty (`f_832027f0`); its work is in slot 40 | read |

### Binding parameters

A class's slot 28 calls one binder per parameter: `(node, &name, field, report)`, where `name` points to the parameter's
name and `field` is where the node keeps it. The binder finds the name among the node's parameters of that type and kind
in the file and stores, at `field`, where the value lives (an output's pointer is tagged in its low 2 bits). With
`report` set, a missing name is reported. The counts are of the slot-28 functions that call each. **read**

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_803f5b40` | `Node_BindBoolOutput` | A Bool output (count at params `+76`); used by 1,498 classes | read |
| `f_803f94c0` | `Node_BindBoolInput` | A Bool input (`+68`); 998 classes | read |
| `f_803f7c70` | `Node_BindIntInput` | An Int input (`+52`); 1,174 classes | read |
| `f_805bde80` | `Node_BindIntOutput` | An Int output (`+60`); 458 classes | read |
| `f_806b6540` | `Node_BindFloatInput` | A Float input (`+84`); 753 classes | read |
| `f_804b4650` | `Node_BindFloatOutput` | A Float output (`+92`); 334 classes | read |
| `f_803f6c80` | `Node_BindStringInput` | A String input (`+100`); 1,000 classes | read |
| `f_808307c0` | `Node_BindStringOutput` | A String output (`+108`); 147 classes | read |
| `f_804b31a0` | `Node_BindVec3Input` | A Vec3 input (`+116`); 624 classes | read |
| `f_805bc980` | `Node_BindVec3Output` | A Vec3 output (`+124`); 522 classes | read |
| `f_805bafc0` | `Node_BindPtrInput` | A pointer (user-defined) input (`+132`); 537 classes | read |
| `f_805bd1b0` | `Node_BindPtrOutput` | A pointer output (`+140`); 297 classes | read |
| `f_804b5440` | `Node_BindBoolFixed` | A fixed Bool parameter (`+12`), such as SeqOneShotSetMQMode's IsIgnoreSequenceCheckFlag; 521 classes | read |
| `f_804b4b00` | `Node_BindFloatFixed` | A fixed Float parameter (`+20`); 137 classes | read |
| `f_804b5a60` | `Node_BindStringFixed` | A fixed String parameter (`+28`); 90 classes | read |

### Two nodes in full

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_832e0660` | `SeqExecuteGenericWaitFrame_OnEnter` | Zeroes the counter (a float at `+32`) | read |
| `f_832e0670` | `SeqExecuteGenericWaitFrame_OnUpdate` | Adds the frame-time keeper's step (`[[0x84c9e890]]+16`, [frame-time.md](frame-time.md)) to the counter, and once the counter reaches the "Frame" input (an s32 through `+40`) marks the node done (status 2) | read |
| `f_832f1070` | `SeqOneShotEnd_Calc` | Marks the node done and nothing else. Its slot 28 (`f_832f1090`) declares nothing. The most used Sequence node (52 of the 108 Sequence files) | read |
| `f_80193e60` | `SeqQueryCalcStringHashMurMur3_Calc` | Unless IsCalcHashOnInit (`+40`) is set, hashes the String input (`+48`) with `Murmur3_String` into `+32`, which the MurMur3Hash output (`+64`) points to. Used by 19 Sequence files | read |
| `f_832e2270` | `SeqExecuteLuaScript_OnEnter` | Sets the status to 3 at once; the node (input ScriptPath) does nothing else, and no loose file uses it | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84c9da38` | `g_NodeRegistryHolder` | Holds the node registry's list (`f_80ca4a00`); its guard is at `[0x84c9da40]` | read |
| `0x84ea5350` | `g_NodeFactory` | The node factory table: `+0` the keys, `+8` the create functions, `+16` the number filled, `+20` the capacity (11,432: twice the 5,716 entries) | read |
| `0x84c9dca8` | `g_AttachmentRegistryHolder` | Holds the second registry's list (`f_80fd67d0`) | read |
| `0x84ea5138` | `g_AttachmentFactory` | The second factory table, the same layout | read |
| `0x84df1098` | `NodeRegistryEntries` | The first of the 32-byte registry entries, which run to `0x84ea5520`: Logic's from `0x84e61d80` to `0x84e63b30`, Sequence's from `0x84e8d108` to `0x84e93e18`, CommonNode's and CommonSequence's from `0x84e96e38` to `0x84e97b18`, the AI ones around them | read |
| `0x848709e8` | `InitArray_Nodes` | Where the init array's node initialisers start; they run to `0x8487f368` | read |
| `0x84c92ad8` | `SequenceComponent_vtable` | The vtable of the component named "Sequence"; slot 18 is `f_83bdd800` | guess |
| `0x84c7a7c8` | `AIModule_vtable` | The module vtable with `f_80fd0330` (slot 7) and `f_80fd5d00` (slot 9) | guess |

## Structures

### A registry entry (32 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0` | The key (u32): murmur3(name) + murmur3(category) | read |
| `+8` | The create function: called with the heap (or 0) in `x0`, returns the node | read |
| `+16` | The link: the next link | read |
| `+24` | The link: the list or the link before | read |

### A registry list

| Offset | Field | Sure |
|---|---|---|
| `+0` | The first link | read |
| `+16` | The count | read |
| `+20` | The link's offset in an entry (16) | read |

### A node: the shared part (32 bytes)

Every class keeps its parameters from `+32`. A create function allocates the class's size (40 bytes for
SeqQueryHasAoCMQ, 0x148 for QueryGameBancParamMQName), zeroes it, sets the u16 at `+30` to 0xff and stores the vtable.

| Offset | Field | Sure |
|---|---|---|
| `+0` | The vtable | read |
| `+8` | The graph instance: `[+8]+16` holds the file, `[+8]+128` and `+136` the count and words of a bit per node (set by Leave, cleared by Enter) | read |
| `+24` | Flags (u32). Bit 1: Update calls the calc again (**guess**: "calc every frame"). Bits 30 and 31: the result, 2 done, 3 (**guess**: failed). `f_8005b560` keeps bit 11 and sets bit 10 when the record's u16 at `+24` is not 0 | read |
| `+28` | The node's index in the file (u16) | read |
| `+30` | A u16 set to 0xff at creation | read |
| `+32` | The first parameter. An input or output is a pointer to its value, with tags in bits 0 and 1 | read |

### A node record in the `.ainb` (60 bytes)

The fields the code reads; the 1,942 loose files are all version 0x408.

| Offset | Field | Sure |
|---|---|---|
| `+0` | The type (u16): 0 for a class found by name (23,953 nodes), other values for the built-in elements (the most common: 104, 3, 7, 204, 2) | read |
| `+2` | The node's index (u16) | read |
| `+6` | Flags (u16; bit 1 and bit 3 tested by `f_8005b560`) | read |
| `+8` | The name: an offset into the string pool | read |
| `+12` | murmur3 of the name | read |
| `+20` | The offset of the node's parameter block | read |

### A node's parameter block

| Offset | Field | Sure |
|---|---|---|
| `+0` … `+47` | The fixed parameters: an (offset, count) pair for each of Int, Bool, Float, String, Vec3 and pointer | read |
| `+48` … `+143` | Inputs and outputs: for each of the six types an (offset, count) pair of inputs and one of outputs (the counts at `+52`/`+60` Int, `+68`/`+76` Bool, `+84`/`+92` Float, `+100`/`+108` String, `+116`/`+124` Vec3, `+132`/`+140` pointer) | read |
| `+148`, `+149` | Two byte counts that other binders (`f_8054e980`, `f_8054e2a0`) step through | read |

## Notes

### The vtable

Counted over the 5,712 classes' vtables. "Shared" means the same function in every class. **read** for the shape;
the roles of slots 0 and 1 are **guess** (they look like sead's run-time type checks: slot 1 returns a static object
made on first call).

| Slot | Offset | What it holds |
|---|---|---|
| 0, 1 | `+0`, `+8` | Per class: a type check and its type object |
| 2, 3 | `+16`, `+24` | The destructor (`f_80c33cd0` for most) and the deleting destructor (per class; calls 2 and frees the class's size) |
| 4 | `+32` | Shared (`f_8005db60`) |
| 5 | `+40` | Shared: Setup (`f_806b7eb0`) |
| 7, 8, 9 | `+56` … `+72` | Shared: Enter, Update, Leave (`f_837cbb30`, `f_837cbb70`, `f_837cbb80`) |
| 19 | `+152` | Per class: the name (returns a string) |
| 20 | `+160` | Per class: the category (`AI`, `Sequence`, `Logic`, `CommonSequence`, `CommonNode`) |
| 21 | `+168` | Shared: the parameter block (`f_8054dd00`) |
| 28 | `+224` | Declares and binds the parameters (`f_82633920`, which declares none, in 676 AI and 103 Sequence classes) |
| 29, 30, 31 | `+232` … `+248` | OnEnter, OnUpdate, OnLeave: the kind's base for queries and one-shots, the class's own for Execute nodes |
| 33 | `+264` | After Setup (`f_8085daa0` in most) |
| 39 | `+312` | The calc: Query, OneShot, Selector, and Logic's first calc |
| 40 | `+320` | Logic only: the calc each Update |

The other slots up to 38 are the same in every class. A Query or OneShot vtable has 40 slots (0 to 39), an Execute one
39 (no slot 39), a Selector 41 or 47, a Logic one 41 or 42. A class with extra virtuals has them after slot 39.

### The categories

| Category | murmur3 | Classes |
|---|---|---|
| `AI` | 0x3374793b | 4,960 |
| `Sequence` | 0xe5937b14 | 417 |
| `Logic` | 0x4f9df290 | 140 |
| `CommonSequence` | 0xe881d9a9 | 102 |
| `CommonNode` | 0x8a871ca9 | 2 |
| (a `.module` name) | | 95 |

A name can have a class in more than one category: 39 names have an `AI` and a `CommonSequence` class (the
`SeqOneShotSetNonStructGameData*` and `SeqQueryGetNonStructGameData*` families, `SeqOneShotLoadScreen`,
`SeqOneShotRequestPause`, …) and 13 a `Sequence` and a `CommonSequence` one (`SeqOneShotRequestEvent`, …). A Sequence
graph tries `AI` before `Sequence` before `CommonSequence`, so as read the `CommonSequence` copy of those 52 names is
never the one made. **read** (the order; that nothing else picks the copies is a **guess**)

### The catalogue

The classes by prefix (each name and category once; 5,712 classes from 5,716 entries). A few names break the pattern
(`Oneshot`, `OnShot`, `Exexute`): they are counted with their prefix. **read**

| Prefix | AI | Sequence | CommonSequence | Logic | Other | All |
|---|---|---|---|---|---|---|
| `Query` | 2,084 | | | | 2 (CommonNode) | 2,086 |
| `Execute` | 1,262 | 2 | | | | 1,264 |
| `OneShot` | 1,200 | | | | | 1,200 |
| `Selector` | 250 | | | | | 250 |
| `SeqOneShot` | 59 | 226 | 46 | | | 331 |
| `SeqQuery` | 48 | 95 | 22 | | | 165 |
| `SeqExecute` | 24 | 78 | 31 | | | 133 |
| `SeqSelector` | | 5 | 2 | | | 7 |
| `Seq` (others: `SeqGetPlayerPos`, `SeqCalcSavePlayerForward`, …) | | 8 | | | | 8 |
| `Hold` | | | | 86 | | 86 |
| `Trigger` | | | | 32 | | 32 |
| `Operate` | | | | 19 | | 19 |
| Compiled modules (`…Module`) | 89 | 2 | 1 | | | 92 |
| Others (`DrawSphere`, `GetNextLinearVelocity`, …) | 34 | 2 | | 3 | | 39 |

By area, by the first matching word after the prefix (a keyword count, so a rough guide): Player 487, Physics (CCT,
rigid bodies, ragdolls) 331, UI (screens, layouts, messages, fades) 306, Actor 264, Animation (AS) 239,
Sensor/Perception 216, Event 215, GameData 197, AISchedule 192, Math 154, Npc 135, Pouch and items 123, Sound 121,
Generic 113, Horse 86, Wm (the world manager) 70, PathFinding 69, XLink and ELink 63, Enemy 60, Camera 60,
Blackboard 55, Banc 54, Debug 36. The other 2,066 are mostly one actor's or boss's own (Dark Link, the Poe sisters,
Morpha, Twinrova, Navi, the mini-games). Of the 644 `Seq*` classes: GameData 118, UI 92, Event 34, Banc 31, Player 20,
Actor 12, Wm 10, Blackboard 10. **read**

**What the shipped graphs use** (the 1,942 loose `.ainb`: 12 in `AI/`, 1,822 in `Logic/`, 108 in `Sequence/`): 1,636
names, of which 895 are registered classes, 316 are `.module` references to other graphs, and 425 have no class:
`ActorLogic*` (208 names, 3,294 uses; `ActorLogicAreaActor` alone is in 1,112 files) and `Event*` (`EventQuery*`,
`EventTrigger*`, `EventPlayAS`, …). 4,765 registered classes are not in the loose files; the AI graphs are in the
AI.Global pack and the actors' packs, which were not scanned. **read** (the counts); **guess** that the 425 are found
through `f_8005b560`'s name-hash tree

Some nodes by area. "Calc" is slot 39, or OnEnter/OnUpdate for an Execute node. "Files" counts the loose files that
use the name. Nodes with rows on other pages are listed after the table.

| Node | Category | Vtable | Name (slot 19) | Calc | Files |
|---|---|---|---|---|---|
| `SeqExecuteGenericDummyAction` | AI | `0x84bc62b8` | `f_832e05a0` | OnEnter `f_832e04d0`, OnUpdate `f_832e04f0` | 39 |
| `SeqExecuteSetSequenceCalcSystem` | Sequence | `0x84bc8698` | `f_832e4d00` | OnEnter `f_832e4bf0`, OnUpdate `f_832e4c30` (input IsLowPriority) | 1 |
| `SeqOneShotLoadScreen` | AI | `0x84bd3448` | `f_832f6e90` | `f_809e15c0` | 20 |
| `SeqOneShotOpenScreen` | Sequence | `0x84bd4a98` | `f_832f8ec0` | `f_80c86420` | 20 |
| `SeqOneShotCloseScreen` | Sequence | `0x84bce368` | `f_832ee790` | `f_80ba0e60` | 31 |
| `SeqExecuteLoadAndOpenScreen` | Sequence | `0x84bc67d8` | `f_832e1090` | OnEnter `f_832e0e90`, OnUpdate `f_832e0f60` | 9 |
| `SeqOneShotSetCurrentFullScreenUIType` | Sequence | `0x84bda3d8` | `f_83300a50` | `f_803faeb0` | 19 |
| `SeqOneShotSetNonStructGameDataBool` | AI | `0x84bddf68` | `f_833069b0` | `f_80ad0e90` | 41 |
| `SeqQueryGetNonStructGameDataBool` | AI | `0x84be84a8` | `f_83317470` | `f_808aaa50` (shared with the other getters) | 36 |
| `HoldGameDataGetBoolSimple` | Logic | `0x84bae6d0` | `f_83200c00` | `f_83200b80`, each Update `f_80851a80` | 854 |
| `TriggerGameDataSetBoolTrue` | Logic | `0x84bb78c8` | `f_832113e0` | `f_83211360`, each Update `f_80ce2ac0` | 709 |
| `SeqExecuteSaveGameData` | Sequence | `0x84bc7da0` | `f_832e3eb0` | OnEnter `f_832e3e20`, OnUpdate `f_80fac8e0` | 5 |
| `SeqOneShotRequestEvent` | Sequence | `0x84bd6628` | `f_832fb330` | `f_832fb1a0` | 6 |
| `SeqQueryIsEventRunning` | CommonSequence | `0x84c92430` | `f_83bdc5f0` | `f_83bdc4a0` | 0 |
| `OneShotEventMemberRequestEventByEventName` | AI | `0x84a20138` | `f_828f4d90` | `f_828f4c20` | 0 |
| `SeqOneShotSetPlayerPos` | Sequence | `0x84bdf278` | `f_833080c0` | `f_80964cf0` | 1 |
| `SeqGetPlayerPos` | Sequence | `0x84be3828` | `f_8330d840` | `f_8330d7a0` | 1 |
| `OneShotPhysicsCctChangePosition` | AI | `0x84a36a78` | `f_82920220` | `f_82920120` | 0 |
| `SeqOneShotCreatePouchPlayer` | Sequence | `0x84bce758` | `f_832eeb70` | `f_80c384a0` | 1 |
| `SeqOneShotCreateUICamera` | Sequence | `0x84bcec90` | `f_832ef3d0` | `f_80c382f0` | 1 |
| `SeqOneShotSetUICameraState` | Sequence | `0x84be0398` | `f_83309660` | `f_833095a0` | 3 |
| `ExecutePlayerCameraChase` | AI | `0x849de0b8` | `f_827c4170` | OnEnter `f_827c2510`, OnUpdate `f_80738670` | 0 |
| `SeqOneShotStartSound` | Sequence | `0x84be15f8` | `f_8330acd0` | `f_80e02110` | 7 |
| `SeqOneShotResetSound` | Sequence | `0x84bd8d88` | `f_832fe4f0` | `f_832fe3e0` | 0 |
| `SeqOneShotRequestLoadBancAsync` | CommonSequence | `0x84c8e0b8` | `f_83bd41a0` | OnEnter `f_83bd3f90`, OnUpdate `f_83bd3ff0` | 0 |
| `SeqExecuteWaitLoadBancAsyncEnd` | CommonSequence | `0x84c8c378` | `f_83bd0f20` | OnEnter `f_83bd0e00`, OnUpdate `f_83bd0e30` | 0 |
| `SeqQueryGetCurrentGameBancParam` | Sequence | `0x84be5a38` | `f_83311f40` | OnEnter `f_83311b80`, OnUpdate `f_83311dc0` | 5 |
| `ExecuteWmSetTime` | AI | `0x84a02b58` | `f_828b0f90` | OnEnter `f_828b0e80` (inputs TimeHour, TimeMin) | 0 |
| `SeqOneShotInitializeWorldMgr` | Sequence | `0x84bd2dc0` | `f_832f6850` | `f_8083b0b0` | 1 |
| `HoldWmGetDateTime` | Logic | `0x84bb3060` | `f_83209840` | each Update `f_83209630` | 19 |
| `HoldWmSetEnvPalette` | Logic | `0x84bb35e0` | `f_83209f60` | each Update `f_83209d40` | 43 |
| `SeqOneShotRequestPause` | AI | `0x84bd6e08` | `f_832fbcc0` | `f_80dbaec0` | 10 |
| `SeqOneShotReleasePause` | AI | `0x84bd5ba8` | `f_832fa5d0` | `f_80dcd2c0` | 13 |
| `OperateAND` | Logic | `0x84bb4910` | `f_8320c4f0` | `f_80860a10`, each Update `f_8320c290` | 695 |

Nodes with rows on other pages: SeqQueryHasAoCMQ, SeqQueryIsExistSave, SeqOneShotSetMQMode,
SeqOneShotOpenSystemWindow, OneShotOpenSystemWindow and QueryGameBancParamMQName ([master-quest.md](master-quest.md));
QueryBancChangeEventRequestArg (`f_829df380`, [events.md](events.md)); SeqOneShotOnChangeBancOptimizeChangeBanc and
QueryBancStartPosByName ([maps.md](maps.md)); SeqQueryGameDataGetManualSaveIndex and SeqExecuteLoadSaveData (its slot 29,
`f_832e1db0`; [save-data.md](save-data.md)); SeqQueryIsClosableLoadListScreen ([save-list.md](save-list.md));
OneShotAddOrRemovePouchContent and QueryGetPouchContentNumByActorName ([player.md](player.md));
QueryGenericIsVisibleByOcclusionQuery and SeqOneShotSetOcclusionCullingFieldType ([culling.md](culling.md));
SeqOneShotClearTextureCacheAll ([textures.md](textures.md)); SeqOneShotSetDefaultVoiceLanguage ([audio.md](audio.md)).

### How a node runs

The graph calls a node's Enter (slot 7) when it starts and its Update (slot 8) each tick, and Leave (slot 9) when it is
done. Enter and Leave keep a bit per node in the graph (cleared on Enter, set on Leave), and call the class's
OnEnter/OnUpdate/OnLeave (29/30/31). The kind's base class turns these into calc calls:

- **A one-shot** calcs once, on Enter. Its Update does nothing (the Sequence and CommonSequence ones) or calcs again when
  bit 1 of `+24` is set (the AI ones).
- **A query** calcs on Enter, and again on each Update only when bit 1 of `+24` is set. An AI query is marked done after
  its first calc. So a query's outputs are not fresh every frame unless the graph asks for it.
- **An Execute node** has its own OnEnter and OnUpdate and sets its status (2 or 3) itself when it finishes; the graph
  keeps calling Update until then.
- **A Logic node** calcs (slot 39) on Enter and runs slot 40 on each Update, with a float from the call's argument.

The argument OnEnter and OnUpdate get is 8 bytes and a float (two floats for the AI one-shots); what it holds was not
followed. Which code calls slots 7 and 8 on a graph's root each frame was not found (see *Open questions*). **read**

### LS

`host/hos_main.c` reaches into these classes for the debug tools. **read** (from the code; the tools themselves are
**tested** on their own pages)

- **Pouch edits** call `OneShotAddOrRemovePouchContent`'s calc (`f_828d4a70`, slot 39 of `0x84a10af8`) on a node it lays
  out on the stack: the inputs' pointers at `+32` and on (the actor name at `+0x80`, "Script", the delta), and it reads
  the result from bits 30 and 31 of `+24`, which is the shared node layout above. Because the fake node has no vtable
  and no graph at `+8`, this works only while the calc uses neither.
- **Counting** a pouch content calls `Pouch_Count` (`f_805b63e0`), the function `QueryGetPouchContentNumByActorName`'s
  calc uses, not the node.
- **Set time** calls `WorldMgr_SetTime` (`f_834722d0`), `ExecuteWmSetTime`'s call, with hours × 3600; the node itself
  takes TimeHour and TimeMin.
- **The teleport** builds the event request `SeqOneShotRequestEvent` builds (the event manager, a 0x1b20-byte
  argument) and releases its two actor links the same way ([events.md](events.md)).
- **Flight and the position mark** follow `OneShotPhysicsCctChangePosition` to Link's controller and call
  `f_806d2530` ([player.md](player.md)).

## Open questions

- **The per-frame driver.** Which function calls a graph's root Enter and Update each frame, and on which thread. Callers
  of slot 7 or 8 in the engine's code include `f_80041580`, `f_8005aba0`, `f_80555fa0`, `f_8055dea0` and the forwarding
  object with the vtable `0x84c7a218` (`f_83ad7700`…`f_83ad7730` call `[+8]`'s slots 7 to 10). [actors.md](actors.md)
  has the AI groups' job; the Sequence controller's calc was not traced.
- **The argument** OnEnter and OnUpdate get (8 bytes and one or two floats): is the float the frame's step? That
  matters above 60 fps: Logic nodes' slot 40 gets it each Update.
- **What sets bit 1 of `+24`** (a query that calcs every Update), and what the record's flags at `+6` mean.
- **The 425 names with no class** (`ActorLogic*`, `Event*`): how `f_8005b560`'s tree (tag 0xd702d6b0) finds them.
- **The 95 compiled modules' names:** the key is murmur3 of `<Name>.module` plus the category, but the names of the 64
  not found are written differently from the class names (`EnemyCommonCommonSwitchBattleTargetIfNeededModule`).
- **The copies:** whether anything makes the `CommonSequence` class of a name that also has an `AI` or `Sequence` class.
- **The second registry's classes:** that they are the `.ainb` attachments is from their names only.
- **The packed graphs:** the AI.Global pack and the actors' packs were not scanned, so the AI node counts above are of
  the loose files only.
