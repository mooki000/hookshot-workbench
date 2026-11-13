# The save list

The list of saves behind Continue on the title and Load in the pause menu, the save cards it shows, the confirm screen after
a pick, and the game's generic scrolling list underneath. Looked at for the save slots work (November 2026).

**Longshot code:** none yet
**See also:** [save-data.md](save-data.md) (the captions the cards show), [layout.md](layout.md) (panes, text, animations,
capture panes)

## The save list screen

`UISystemLoadListScreen` is the list's screen. Its layout is `SystemLoadList_00`: a background, a slide list
`N_SlideList_00` and a scroll bar.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83420ef0` | `LoadListScreen_Factory_Name` | The factory's name getter: returns "UISystemLoadListScreen" | read |
| `f_83420f00` | `LoadListScreen_Create` | The factory's create: makes the screen object (0x2c8 bytes, vtable `0x84c0eba0`) | read |
| `f_83420810` | `LoadListScreen_OnOpen` | Vtable slot 98, run as the list opens. Sets the list's length to the caption manager's slot capacity: `f_83793510(wrapper, load32(arr + 4))`, 6, at 0x834208a4. Then restores the cursor to the last picked index (the game data int 0xa2995097, read by `f_80857070`, clamped to the count) and moves it there with `f_837935d0` | tested |
| `f_83420b40` | `LoadListScreen_MakeSlideList` | Vtable slot 35: a factory for the pane named "N_SlideList_00". Allocates the list's 0x80-byte wrapper (vtables `0x84c0ef08` / `0x84c0efd8`) and keeps it at `screen+688` | read |
| `f_83420410` | `LoadListScreen_WrapCards` | Vtable slot 93: wraps the slide list's card parts, each through `f_834206d0` | read |
| `f_834206d0` | `LoadListScreen_WrapCard` | Wraps one card part of the slide list in a card object (the array: count at `wrapper+96`, pointers at `+104`) | read |
| `f_83420c70` | `LoadListScreen_FillEntry({entry, pane})` | The slide list's "fill this entry" callback (the wrapper's vtable slot 18). Finds the card for the pane (`f_8379beb0`) and tail-calls `f_833ec800(card, entry)` | tested |
| `f_83420ab0` | `LoadListScreen_OnDecide` | Vtable slot 50: A on a card. Gets the entry from the pane (`f_83793b10`), calls `f_833ee110(card, pane, entry)`, then, unless the card is disabled (`f_83791f90`), clears `screen+704` | tested |
| `f_834209d0` | `LoadListScreen_OnCursor` | Vtable slot 46: the cursor moved to a card. Plays the card's CursorLoop (`f_833ee1e0`) and writes the focused slot (or -1) to `[0x849e5eb0]+1344` | read |
| `f_83420970` | `LoadListScreen_vt104` | Vtable slot 104: sets `screen+704` to 1 | read |
| `f_802f0fd0` | `UIScreen_OnAnimRequest` | Slot 11 (+88) of the screen's vtable, the "animation request" hook; empty | read |
| `f_8331b8c0` | `SeqQueryIsClosableLoadListScreen_Declare` | Declares the sequence query SeqQueryIsClosableLoadListScreen (output "IsClosable") | read |

## The confirm screen

The save confirm screen is a separate screen, `UISystemSaveLoadScreen` (layout `SystemSaveLoad_00`: one card
`Pa_DataFile_00` and the Yes/No buttons `Pa_CheckButton_00/01`).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_83425080` | `SaveLoadScreen_Fill` | Fills the confirm screen. In mode `+684 == 1` (load): reads the picked slot from CaptionData and the index 0xa2995097, fills the card with `f_833ec800`, shows message 1001. Otherwise (save): fills the card from the current game (`f_833ed200`), message 1000 | read |
| `f_83424eb0` | `SaveLoadScreen_Setup` | Part of the screen's setup | read |
| `f_83425520` | `SaveLoadScreen_Setup2` | Part of the screen's setup | read |

## Save cards

Each entry is a part `PaDataFileBtn_00` (a 2200×424 card) wrapped in a 0x290-byte card object, made by `f_833eb450` and
initialised by `f_833eb610`. The initialiser looks up the card's animations and parts by name and keeps them in the card (see
[Structures](#structures)).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_833eb450` | `SaveCard_Create` | Makes a card object (0x290 bytes) | read |
| `f_833eb610` | `SaveCard_Init` | Initialises a card: looks up its animations and parts by name (the card's fields below) | read |
| `f_833ec800` | `SaveCard_Fill(card, entry)` | Fills a card for an entry. Entry < 0: hides it. Otherwise stores it at `card+600` and looks up the slot's caption (`f_833cd490`). If the lookup fails, returns without touching the card, so a reused card keeps what it showed. If the record is unusable (`rec+640` bit 1 clear, or no screenshot, `rec+240 == -1`): the empty look (Failure, `card+112`) and disabled. Otherwise: Decide on layer 4 and `f_833ec8f0` | tested |
| `f_833ec8f0` | `SaveCard_FillFromRecord(card)` | The full fill. Screenshot (`rec+240` → `f_8045b700`, `f_80e6b660`, PicLoad at 1 with `f_80bcce80`), date (`rec+448…464` into message arguments with `f_80183b70`, Time_AM/PM from `LayoutMsg/SystemSaveLoad_00`, into `T_Day_00`), place (`rec+480` through `f_80a8a480` into `T_Place_00`), AutoSave (`rec+472`), Clear (`rec+473`), hearts (`rec+1408`, `+1412`, `+1432`), medallions (`rec+1384…`) | read |
| `f_833ed200` | `SaveCard_FillFromGame(card)` | Fills a card from the game being played, for the save confirm screen | read |
| `f_833d92c0` | `SaveCard_SetHeart(heart, health, max)` | Sets one heart part: its fill (`heart+56`) and whether it shows (`heart+64`). A heart past the maximum gets both stopped | read |
| `f_833ee110` | `SaveCard_Pick(card, pane, entry)` | The pick: the slot (`rec+4`, or -1) into the blackboard's CaptionData (`[0x84ea42a8]`, struct hash 0x46926d32, field 0xd13d898b, through `f_809d3eb0` / `f_809d39e0`), and the entry into the int 0xa2995097 | read |
| `f_833ee1e0` | `SaveCard_PlayCursorLoop` | The cursor's glow on a card (CursorLoop) | read |
| `f_83791f70` | `Part_SetDisabled(part, on)` | The part vtable's +320: disables a card by playing (1) or stopping (0) the part's animation at `part+80`. It is an animation, not a visibility switch | tested |
| `f_83791f90` | `Part_IsDisabled(part)` | Whether a part is disabled (the save list asks it before clearing `screen+704`) | read |
| `f_80df53e0` | `AnimCtl_SetLayer(ctl, layer, anim)` | Puts an animation into slot `layer` of a part's animation control (`part+72`); layer 4 holds the card's Decide or Failure | read |

## Slide lists

The game's generic scrolling list. Every slide list in the game goes through `f_8151eb00`.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_81549620` | `LayoutBuilder_Build` | The layout builder; makes a slide list (control type 7) through `f_8155c540` → `f_8151e410` | read |
| `f_8155c540` | `LayoutBuilder_MakeControl` | Between `f_81549620` and `f_8151e410` on the way to a slide list | read |
| `f_8151e410` | `SlideList_Create` | Makes a slide list (control type 7) | read |
| `f_8151e870` | `SlideList_ReadRecord` | Copies the list's record from the layout (columns, rows, card size 2200×424, gap 12) | read |
| `f_8151eb00` | `SlideList_MakeParts` | Works out the list's parts: how many cards fit on screen (`f_8155bb80`) plus the extra parts its controller asks for. Stores the count at `slide+88` | tested |
| `f_8155bb80` | `SlideList_FitCount` | How many cards fit on screen: the pane's height over the pitch (1984 / 436 for the save list, giving 6) | read |
| `f_80c6a490` | `SlideList_PartCount` | Returns `slide+88`, the part count | read |
| `f_83793510` | `SlideList_SetEntryCount(wrapper, n)` | Sets the list's entry count through `f_81524d50`. Only the data count: the parts are made when the layout is built | tested |
| `f_81524d50` | `SlideList_SetEntryCountImpl` | Stores the entry count (`slide+292`) | read |
| `f_837935d0` | `SlideList_MoveCursor` | Moves the list's cursor to an entry | read |
| `f_83793b10` | `SlideList_EntryOfPane(&entry, pane)` | The entry a pane shows | read |
| `f_8379beb0` | `SlideList_CardOfPane` | The card object of a pane | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84c0eba0` | `LoadListScreen_vtable` | The save list screen's vtable (0x2c8-byte objects, made by `f_83420f00`) | read |
| `0x84c0f0d8` | `LoadListScreen_Factory_vtable` | The screen's factory: the name getter `f_83420ef0`, the create `f_83420f00` | read |
| `0x84c0ef08` | `LoadListWrapper_vtable` | One of the slide list wrapper's two vtables (made by `f_83420b40`) | read |
| `0x84c0efd8` | `LoadListWrapper_vtable2` | The wrapper's other vtable | read |
| `0x84c416e8` | `CardPart_vtable` | The vtable of a card's part pane (`card+48`) | read |
| `0x849e5eb0` | `g_SaveListFocus` | A pointer to the object whose `+1344` holds the save list's focused slot (or -1) | read |

## Structures

### The save list screen (0x2c8 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+684` | The confirm screen's mode: 1 load, otherwise save (on `UISystemSaveLoadScreen`) | read |
| `+688` | The slide list's wrapper (made by `f_83420b40`) | read |
| `+704` | Set by vtable slot 104 (`f_83420970`), cleared by a pick on a card that is not disabled | read |

### The slide list wrapper (0x80 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+96` | The number of card objects | read |
| `+104` | The card objects' pointers | read |

### A slide list

| Offset | Field | Sure |
|---|---|---|
| `+88` | The number of parts (cards made) | tested |
| `+292` | The number of entries | tested |

### A save card (0x290 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+32` | The card's layout (texts are set through it) | read |
| `+48` | The card's part pane (vtable `0x84c416e8`) | read |
| `+56` | Animation "Decide" | read |
| `+64` | Animation "AutoSave" (the Autosave badge; one frame long: 1 shows the badge) | read |
| `+72` | Animation "Invalid" | read |
| `+80` | Animation "PicLoad" (the screenshot's fade-in) | read |
| `+88` | Animation "Clear" (the clear icon) | read |
| `+96` | Animation "HardMode" | read |
| `+104` | Animation "ImportantState" (the medallion and stone frame) | read |
| `+112` | Animation "Failure" (an empty or broken slot) | read |
| `+120` | Animation "CursorLoop" | read |
| `+128 … +192` | The 9 medallion and stone icon parts (`Pa_ImportantIcon_%02d`) | read |
| `+200 … +352` | The 20 heart parts (`Pa_Heart_%02d`) | read |
| `+360` | The screenshot pane `P_GameCapture_00` | read |
| `+600` | The entry the card shows | tested |

## Notes

- The list hands entries to its on-screen cards in turn, so with more entries than cards a card is reused. A reused card
  keeps what it showed when its caption lookup fails (`f_833ec800`), and what is drawn into its capture pane can't be changed
  afterwards ([layout.md](layout.md#capture-panes)).
- An extra part made with `f_8151eb00` is not used for entries: it sits unused at the top (a gap).
- The list's length is the caption manager's capacity, 6 ([save-data.md](save-data.md)).
