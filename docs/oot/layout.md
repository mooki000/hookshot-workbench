# Layout panes, text and animations

The game's UI is built from layouts: trees of panes (text, pictures, parts, captures) with animations that show, hide and
move them. These are the functions for finding a pane, setting its text and playing its animations, as the save list, the
menu mouse and the text speed and size options use them.

**Longshot code:** [host/oot_menu.c](../../host/oot_menu.c) (panes by name and their positions),
[nx2/oot_patches.py](../../nx2/oot_patches.py) (text speed and size), [host/nn_vi.c](../../host/nn_vi.c) (`hos_text_scale`)
**See also:** [save-list.md](save-list.md), [menus.md](menus.md)

## Panes and text

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8045c040` | `Layout_PaneTable(layout + 64)` | A layout's pane table: `+256` the array (0xb0 bytes a pane), `+316` the count | tested |
| `f_8045b1f0` | `PaneTable_At(table, i)` | Pane `i` of the table. A pane's flags are at `+168`; bit 0 is "shown" for text panes and the root, but not for panes inside a capture (below) | tested |
| `f_8045b790` | `Layout_FindTextPane(layout, name)` | Finds a **text** pane by name (it skips other kinds) | read |
| `f_8045c4a0` | `Layout_ResolvePath(path, layout, &owner)` | Resolves a pane path ("Pa_Info_00/T_Caption_00"): returns the last name and the layout that holds it | tested |
| `f_8045a9d0` | `TextPane_NameHolder(pane)` | With `f_8045b2a0`, a text pane's name: `f_8045b2a0(f_8045a9d0(pane)+8)`. Other kinds of pane keep no name there | tested |
| `f_8045b2a0` | `TextPane_NameString` | The second half of a text pane's name (above) | tested |
| `f_80a87d30` | `Layout_SetText(layout, pane name, &text, 1, 0)` | Sets a text pane's string. `text` is `{UTF-16 data, length in bytes (32-bit), -1}` | tested |
| `f_8045b970` | `Layout_SetTextWorker` | The text setter's worker (loops over the text panes) | read |
| `f_80a8a480` | `Text_FromMessageName(&text, &source)` | Makes a text from `source` = `{char *data, int size}`, **as a message's name**: a plain string gives "MsgNotFound" | tested |
| `f_80a8afe0` | `Text_FromMessage` | Makes a text from a message file and label, with arguments | read |
| `f_80183b70` | `MsgArgs_Set(ctx, value, hash)` | Sets a message argument (by murmur3 hash) | read |
| `f_8379ac00` | `TextPane_FitText` | Fits a text pane's text to its box | read |
| `f_8078e2b0` | `Layout_FindPane(layout, name)` | A pane by name, of any kind (the menu mouse finds the buttons with it) | tested |
| `f_8078fcb0` | `Pane_Position(pane)` | A pane's position on screen, in `s0` and `s1` | tested |
| `f_8152b670` | `TextBox_Draw(box)` | A text box's draw, each time the game shows it (`x0` the box). Hooked by `patch_text_draw`: `hos_prompts_text_draw` first rewrites the box's button-prompt codes when the prompts changed while the game runs, and marks the box to be laid out again (`host/prompts.c`) | read |
| `f_8152aec0` | `TextBox_Layout(box)` | A text box's layout (`x0` the box); it lays its text out again when its status at `+8` has bit 0x20000. Hooked by `patch_text_layout`, which does the same as the draw's hook first (`hos_prompts_text_layout`) | read |
| `f_81680c60` | `ft_smooth_render(…, slot)` | FreeType's smooth glyph renderer (`x1` the glyph slot). Hooked by `patch_glyph`: `hos_prompts_glyph` learns where the game keeps the button prompts' font, for a change while it runs | read |

## The letter animation

Dialogue, Navi and signs show their text letter by letter: the panes with "LetterAnimOn" user data have a LetterAnimControl.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80b1e140` | `LetterAnimControl_Step(ctl, s0 = time)` | Steps a text pane's letter animation. The time moves the letters' clock (`+0x60 += rate +0x4c * time`, the rate from the message's speed tag) and the pauses' timer (`+0x58 -= time`); the auto-advance timer (`+0x5c`) is stepped before them, apart. Hooked by `patch_text_speed`: the time is multiplied by `hos_text_scale` (1.0 as shipped), and the pane is handed to the host for the text size | tested |
| `f_80794b00` | `LetterAnimControl_SkipOnA` | The game's own skip on A: sets `+0x72` to 2, which shows the page at once. A huge text scale has the same effect without its flags | read |

## Animations

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_802f0f00` | `Anim_Show(anim)` | Puts the animation at its last frame, enabled, and queues it (request 5). The game's "show" | tested |
| `f_802f0fe0` | `Anim_Hide(anim)` | Puts it at frame 0, enabled, and queues it (request 4). The game's "hide", but it only works on a card that has not been changed before (see [Capture panes](#capture-panes)) | tested |
| `f_802f2d90` | `Anim_SetFrame(anim, s0 = frame)` | Puts it at a given frame (heart fills, icon kinds) | read |
| `f_802ee030` | `Anim_Play(anim, loop, s0 = speed)` | Plays it over time at a speed (negative: backwards) | read |
| `f_80bcce80` | `Anim_PlayFade(anim, s0 = speed)` | Plays the screenshot's fade: `f_802ee030` with a layout flag (`f_8045bf30`) | tested |
| `f_802f1340` | `AnimPlayer_Length(player)` | The player's length | read |
| `f_802f1350` | `AnimPlayer_SetFrame(player, s0)` | The player's frame | read |
| `f_802f2b80` | `AnimPlayer_SetEnabled(player, on)` | The player's enabled bit | read |

## Capture panes

A card's base, hearts, Autosave badge and screenshot are drawn into a texture by the capture pane `C_Base_00`, and that texture
is shown by `P_Base_00` (run-time pane 31). The capture is taken once, so changing those parts afterwards does not show: hiding
them, stopping or reversing their animations and clearing their flags all failed on a reused card (tested). The card's text,
its divider and the medallion frame are drawn live, on top.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8154ec00` | `CapturePane_Draw` | The capture pane's draw (a 1.7 KB stack frame) | read |
| `f_8154ebc0` | `CapturePane_vtA` | One of the capture pane's other methods (four of them are empty) | read |
| `f_8154ebf0` | `CapturePane_vtB` | Another of its methods | read |
| `f_8154fc30` | `CapturePane_vtC` | Another of its methods | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x8489ad88` | `CapturePane_vtable` | The capture pane's object (`pane+8`); its word 20 is its texture, 256×208 | tested |

## Structures

### An animation object

| Offset | Field | Sure |
|---|---|---|
| `+16` | A request (32-bit: 4 hide, 5 show) | tested |
| `+20` | Flags | read |
| `+24` | The owner | read |
| `+32` | The player | read |

### An animation player

| Offset | Field | Sure |
|---|---|---|
| `+24` | The frame (float) | read |
| `+28` | Bit 0: enabled | read |

### A LetterAnimControl

| Offset | Field | Sure |
|---|---|---|
| `+0x4c` | The letters' rate (from the message's speed tag) | tested |
| `+0x58` | The pauses' timer | tested |
| `+0x5c` | The auto-advance timer | read |
| `+0x60` | The letters' clock | tested |
| `+0x72` | 2: show the page at once (the skip on A) | read |

## Notes

- Requests are linked into the layout's list (`owner+40` → `+120`), and the screen applies them in its update.
- The UI shows and hides through animations. Pane flags are not a dependable switch.
- Panes found by their place in the table must be checked: the table's order at run time is not the layout file's, and the
  flags at run time are not the file's either (the file has `0x0255481b` where the game has `0x8011386b`).
