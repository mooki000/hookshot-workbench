# Link: movement, health and items

Link's character controller and its speed, the write that takes his health, his movement update, and the pouch (the items he
carries). Looked at for the Debug tab's rows: movement speed, protected hearts, flight, and adding and removing items.

**LS code:** `nx2/oot_patches.py` (`patch_cct_speed`, `patch_life`, `patch_movement`,
`patch_flight_rotation`), `host/hos_main.c` (`hos_oot_cct_speed`, the flight, the pouch calls)
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

## Movement update and flight

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_82c415e0` | `Player_UpdateMovement` | The player actor's movement update (`x19` the actor, `x25` its matrix). Hooked by `patch_movement`: the host can lift the player (`hos_oot_lift_player`) before its branch on bit 1, and set the pose while flying (`hos_oot_flight_pose`) | tested |
| `f_806d2530` | `Controller_SetTransform(controller, matrix)` | Hands a controller's transform on through an indirect call (`x0` the controller, `x1` the matrix). Hooked by `patch_flight_rotation`: while flying, the host writes the flight's heading into the matrix first | tested |

## The pouch

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_828d4a70` | `OneShotAddOrRemovePouchContent` | The event that adds or removes a pouch content by actor name (its parameters are read through pointers; the delta's sign picks add or remove). The status is the top bits of the result word | tested |
| `f_82a3c260` | `QueryGetPouchContentNumByActorName` | The query of how many of a content the pouch holds, through `f_805b63e0` | read |
| `f_805b63e0` | `Pouch_Count(manager, &name, 0, 0)` | Counts one content | tested |

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
