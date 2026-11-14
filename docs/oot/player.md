# Link: movement, health and items

Link's character controller and its speed, the write that takes his health, his movement update, and the pouch (the items he
carries). Looked at for the Debug tab's rows: movement speed, protected hearts, flight, and adding and removing items;
also for co-op's native attack observations, equipment display and pouch selection.

**LS code:** `nx2/oot_patches.py` (`patch_cct_speed`, `patch_life`, `patch_movement`,
`patch_flight_rotation`, `patch_attacks`), `host/hos_main.c` (`hos_oot_cct_speed`, the flight, the pouch calls),
`host/oot_coop_hits.c`, `host/oot_coop_players.c` (equipment), `host/oot_coop_kit.c` (capacity and selection)
**See also:** [camera.md](camera.md), [events.md](events.md) (the teleport), [game-data.md](game-data.md)

## Movement speed

A character controller's forward speed (`s0`) goes to `[controller+40]+472`, which the physics step moves the body by, with
its collisions.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_809552f0` | `Player_Move(…, x1 = direction)` | The move Link's own walk, run and swim go through (the speed in metres a frame). It inlines the speed setter at two calls, each after it took the controller into `x0`. A climb up a ledge goes through it too, at some 55 m/s for a frame. Hooked by `patch_cct_speed` | tested |
| `f_80559df0` | `Cct_SetForwardSpeed(controller, s0 = speed)` | The controller's speed setter (its vtable +0x70), the speed in metres a second. Hooked by `patch_cct_speed` | tested |
| `f_80964bf0` | `CctComponent_SetForwardSpeed` | Takes the CCT component and inlines the setter (OneShotPhysicsCctSetForwardSpeedMpf and others). Hooked by `patch_cct_speed` | tested |

## Health

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_804bd8c0` | `Actor_WriteLife` | Writes an actor's life with a compare-and-swap (`x19` the actor; the life at `[actor+7184]+8`). Hooked by `patch_life`: with the hearts cheat on, a write that would lower the player's life keeps it | tested |

## Native attack observations

The rows retain **read** from the supported-build static investigation. Their observation points are in
`nx2/oot_patches.py`, `patch_attacks`, with the host-side interpretation in `host/oot_coop_hits.c`
at `2f01e1b4bdb6e1629c54a998f0403519fe4e5c74`. Observing setup, renewal or off does not establish a swing,
victim contact or actor ownership.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_82ec53d0` | `SensorHit_SetupAttack` | Installs a prepared attack in a bounded sensor record and requests activation. A repeated setup may retain existing IDs; it is not proof of a new swing or victim contact. Hooked by `patch_attacks` after setup | read |
| `f_82ec6680` | `AttackRecord_RenewIds` | Explicitly renews the record's secondary filter ID and its already-present primary ID from the native counter. The hook observes IDs, not established actor ownership. Hooked by `patch_attacks` | read |
| `f_808f56f0` | `SensorHit_RequestOff` | Requests the selected sensor layer off through its physics body; completion is not solver-state readback, and missing bodies need not change state. Hooked by `patch_attacks` | read |

## Movement update and flight

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_82c415e0` | `Player_UpdateMovement` | The player actor's movement update (`x19` the actor, `x25` its matrix). Hooked by `patch_movement`: the host can lift the player (`hos_oot_lift_player`) before its branch on bit 1, and set the pose while flying (`hos_oot_flight_pose`) | tested |
| `f_806d2530` | `Controller_SetTransform(controller, matrix)` | Hands a controller's transform on through an indirect call (`x0` the controller, `x1` the matrix). Hooked by `patch_flight_rotation`: while flying, the host writes the flight's heading into the matrix first | tested |

## The pouch

The capacity and selected-equipment rows retain **read** from the supported-build static investigation;
their call sites are `kit_capacity` and `kit_equip` in `host/oot_coop_kit.c`
at `2f01e1b4bdb6e1629c54a998f0403519fe4e5c74`.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_828d4a70` | `OneShotAddOrRemovePouchContent` | The event that adds or removes a pouch content by actor name (its parameters are read through pointers; the delta's sign picks add or remove). The status is the top bits of the result word | tested |
| `f_82a3c260` | `QueryGetPouchContentNumByActorName` | The query of how many of a content the pouch holds, through `f_805b63e0` | read |
| `f_805b63e0` | `Pouch_Count(manager, &name, 0, 0)` | Counts one content | tested |
| `f_805b5cf0` | `Pouch_CapacityByActorName` | With the pouch manager and a pointer to an actor-name pointer, resolves the content's capacity. Non-count equipment can return a fixed one; stockable contents use their declared capacity source. Missing lookup paths return zero (`kit_capacity`) | read |
| `f_80a9acd0` | `Pouch_SelectEquipmentByActorName` | Resolves a named content's equipment category, queues its selected equipment name through game-data setters, and refreshes the pouch's equipment request state. Native give callers pass manager, name wrapper, policy 3 and flag 0; its answer is not proof that the live equipment actor finished appearing (`kit_equip`) | read |
| `f_8112d570` | `Pouch_AddByActorName(manager, &name, meta, quantity)` | The shared give-by-name routine: `x1` points at a word that points at the actor-name string, `w3` is the quantity. It can issue further gives itself, so one call is not always one item | read |
| `f_8112d370` | `SeqOneShotPushToPouch` | Reads the name and the quantity through the node's tagged pointers at `+0x20` and `+0x30`, then calls `f_8112d570` | read |
| `f_80c8f590` | `OneShotPouchContentPushToPouchOwnInEvent` | A picked-up item's event: finds the owning actor's pouch-content component and calls `f_80c8f620` | read |
| `f_80c8f620` | `PouchContent_Push(component)` | Gives the component's owner by its actor name (the owner is `+0x18`, its name `+0x260`) through `f_8112d570`, quantity 1 | read |
| `f_82b00750` | `Chest_FillGetRequest(node, request)` | Fills a chest's get-event request, the helper behind `QuerySetTreasureboxGetEventRequestArg`. The argument named `ContentName` carries the reward's actor name, set through `f_81003fe0` at two places. It picks what the chest asks for; the pouch write comes later, through the rows above | read |
| `f_80d472f0` | `OneShotActorHolderTryRequestCreateActorWithActorFileName` | Creates a holder's content actor (a chest's shown item among others) by actor-file name; the name comes through the tagged pointer at the node's `+0x38` | read |

## Equipment pieces and hand state

These rows retain **read** from the supported-build static investigation of the equipment routines and their native
callers. The host callers are `equipment_read` and `equipment_play` in `host/oot_coop_players.c`
at `2f01e1b4bdb6e1629c54a998f0403519fe4e5c74`. Requesting a visible piece is separate from pouch ownership
or selected-equipment consistency; the latter uses `f_80a9acd0` above.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_82e17b20` | `EquipmentUser_RequestPiece` | Requests a locally named piece for an equipment kind; name is passed by pointer cell, with initial state and variant. Cancels an earlier request for the same kind before creating the replacement. Kinds and hand slots are distinct (`equipment_play`) | read |
| `f_80d07ab0` | `EquipmentUser_GetHandState` | Resolves the live slot handle and reports its effective hand state. Pending requests can override it; after the request byte clears it consults the piece's model-binding state, rather than treating zero as sheathed (`equipment_read`, `equipment_play`) | read |
| `f_80e54c40` | `EquipmentUser_ChangeHandState` | Routes a hand slot's state request, in-hand, sheathed or inactive, to the corresponding native operation (`equipment_play`) | read |
| `f_82e1d1b0` | `EquipmentUser_RemoveHandPiece` | Cancels the slot's outstanding creation and removes its current linked piece, with explicit policy parameters controlling ancillary native behavior (`equipment_play`) | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84c9e2a8` | `g_GameStateHolder` | A state singleton the pouch event needs ready | tested |
| `0x84eb6888` | `g_ActorDb` | The actor database, which the pouch event needs | tested |
| `0x84e8c060` | `g_ActorInfo` | Actor info, which the pouch event needs | tested |
| `0x84e98e28` | `g_PouchMgrHolder` | The pouch manager's holder (for `f_805b63e0`) | tested |
| `0x83f0a6d8` | `str_Script` | The string "Script", the pouch event's caller | tested |

The item database `[[0x84c9e0d0]]` the pouch event loads is on [menus.md](menus.md).

## Structures

### A character controller

| Offset | Field | Sure |
|---|---|---|
| `+40` | Its body; `+472` of that is the forward speed the physics step uses | tested |

## Notes

- `f_82c415e0` (the movement update) also runs the AI component's unit at index `+0x18` ([ai.md](ai.md)). **read**

- An event node's status is bits 30-31 of its `+24`: 2 done, 3 probably failed ([sequences.md](sequences.md)). `host/hos_main.c` calls `OneShotAddOrRemovePouchContent`'s calc on a stand-in node, which works because that calc never touches the node's vtable or its graph at `+8`. **read**
- Only a walk's, run's or swim's speed is scaled. Scaled, the ledge climb's 55 m/s frame threw Link past the ledge. The first
  of the three places inlines the setter, so no speed is scaled twice.
