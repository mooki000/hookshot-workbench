# The menus

The game's menu screens (the title and its lists, the pause menu, Key Items, Threads of Time, the Map, the options, the save
files' dialogs): how a screen updates, how a button gets the focus, and how the pause menu's pages and Key Items find their
panes. Looked at for the menu mouse (point, click and scroll in the game's own menus).

**Longshot code:** [host/oot_menu.c](../../host/oot_menu.c), [nx2/oot_patches.py](../../nx2/oot_patches.py)
(`patch_menu_focus`, `patch_menu_tick`)
**See also:** [layout.md](layout.md) (finding panes and their positions)

## Screens and the focus

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_802f1380` | `MenuScreen_Update(screen)` | Every menu screen's update (the HUD's, the menus', the title's), each frame, before the screen's UI controller (`screen+56`, its vtable's +16) runs the buttons' requests. Hooked by `patch_menu_tick`: the host tracks the screens and gives the mouse to the one in front | tested |
| `f_802ed660` | `PauseMenu_OnFocus(screen, pane)` | The pause menu's handler of a pane getting the focus (in its vtable at `0x84c0bc18`). Matches the pane's name against the pages (PaItemMenu_00, PaEquipmentMenu_00, PaCollectMenu_00, PaSystemMenu_00) and hands the pane to the page. Hooked by `patch_menu_focus`: the host learns the page, for its tabs | tested |
| `f_802ebd20` | `ItemsPage_OnFocus` | The Items page's part of the focus: the info panel | read |
| `f_8088ffc0` | `MenuScreen_SetFocus(screen, button, flag)` | The focus setter, as the game's screens call it. Finds the button's entry in the screen's list (`+136` the head, `+144` the first, `+156` the entry's offset from its node; `entry+24` the button) and hands it to the focus manager (`[screen+32]+96`, its vtable +48), when the manager takes the player's input | tested |
| `f_80795090` | `MenuScreen_FocusCaller` | One of the game's callers of `f_8088ffc0`, the model for how the host calls it | read |
| `f_807923a0` | `MenuPage_Name` | A page's name: `obj+128` → `+248` → `+24` (the host reads the same chain) | read |

## Key Items

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_833fdaa0` | `KeyItems_PlaceCursor` | Finds the page's items where it shows them, to place its cursor: the item database's lists 4 and 3, each entry's key and its pane | read |
| `f_833fda00` | `KeyItems_Refresh(screen, screen+760)` | The page's refresh from the key in its key buffer (`[screen+760]`, its room at `+768`): the cursor's key and the item's name and description. The page's update places the cursor from the key the next frame | tested |
| `f_80a8f690` | `ItemDb_List(db, which)` | One of the item database's lists: `+8` the count, `+16` the entries | tested |
| `f_8002a520` | `ItemEntry_Key` | An entry's key, the first of three calls (`f_8002a520`, `f_80d666a0(…, 0)`, `f_8002a4c0`) that end at a string, its text first | tested |
| `f_80d666a0` | `ItemEntry_Key2(…, 0)` | The second of the three | tested |
| `f_8002a4c0` | `ItemEntry_KeyString` | The third: the key's string | tested |
| `f_80a963c0` | `ItemEntry_PaneName` | The name of an entry's pane (a string, its text first) | tested |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84c0bc18` | `PauseMenu_vtable` | The pause menu's vtable (holds `f_802ed660`) | read |
| `0x84c9e0d0` | `g_ItemDbHolder` | The item database, `[[0x84c9e0d0]]`. The host's pouch calls load the same holder ([player.md](player.md)) | tested |

## Structures

### A menu screen

| Offset | Field | Sure |
|---|---|---|
| `+32` | An object whose `+96` is the focus manager | tested |
| `+40` | The screen's layout | tested |
| `+56` | The UI controller (its vtable +16 runs the buttons' requests) | read |
| `+136`, `+144`, `+156` | The buttons' list: the head, the first, an entry's offset from its node | tested |

### The Key Items page

| Offset | Field | Sure |
|---|---|---|
| `+760` | The key buffer's pointer (`+768` its room) | tested |
| `+840` | 0 while the page browses, 1 while an item's details show | tested |

## Notes

- `HOS_MENU_TRACE=1` logs the focus changes, the screens and what the mouse does.
