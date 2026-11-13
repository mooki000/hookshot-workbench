# The camera

The player camera's update, where its yaw and pitch are and what turns them, how it follows Link by itself, and the gameplay
view's matrix. Looked at for the native mouse look, the option to stop the camera centring itself, and the free camera.

**Longshot code:** [host/mouse_native.c](../../host/mouse_native.c), [nx2/oot_patches.py](../../nx2/oot_patches.py)
(`patch_mouse_camera`, `patch_camera_centering`, `patch_view`), [host/hos_main.c](../../host/hos_main.c) (`hos_oot_free_view`)
**See also:** [player.md](player.md)

## Functions

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80644c80` | `PlayerCamera_Update(camera)` | The player camera's update, a virtual method (the camera in `x0`), once a frame on the game's thread while that camera runs. It calls the stick's turns (below) only while the right stick is out of its dead zone. Hooked by `patch_mouse_camera` (the host turns the camera by the mouse before the camera reads its input) and `patch_camera_centering` (state 3's follow of Link's movement skipped) | tested |
| `f_80649c10` | `PlayerCamera_TurnYaw` | Turns the yaw by the right stick, wrapped at 180 | tested |
| `f_8064b770` | `PlayerCamera_TurnPitch` | Turns the pitch by the right stick, within the limits | tested |
| `f_80642c80` | `PlayerCamera_AuthoredHeading` | The authored heading override in state 3's follow; when it applies, the update branches to 0x806492c4 | read |
| `f_80642410` | `PlayerCamera_EaseYaw` | Eases the yaw (`+0x1a4`) toward a heading; state 3's follow calls it at 0x806475e4 | read |
| `f_804e48c0` | `View_UpdateMatrix(…, x20 = view)` | Builds a view's matrix in a loop (its head at 0x804e5068). Camera zero gives the gameplay view, but the same routine serves the other cameras' views too. Hooked by `patch_view`: with the free camera on, the host builds the gameplay view's matrix instead | tested |
| `f_80643f10` | `CameraSpeed_Yaw` | Returns in `s0` the yaw's turn speed for the right stick: the entry of the game's own camera speed option (options `+0x254`) in its speed table, times a factor. The player camera's update (`f_80644c80`) multiplies the stick by it each frame. Hooked by `patch_camera_speed`: each return scaled by `hos_camera_scale` (the Insert menu's Camera speed, 1.0 as shipped) | read |
| `f_80643b60` | `CameraSpeed_Pitch` | The pitch's, the same option's entry in the other table. Hooked by `patch_camera_speed` | read |
| `f_82ea21f0` | `CameraSpeed_Yaw2` | The yaw's at level 2, the path taken while the stick's actor has the flag at `+0x218`. Hooked by `patch_camera_speed` | read |
| `f_82ea2220` | `CameraSpeed_Pitch2` | The pitch's at level 2. None of the four calls another, so each turn is scaled once. Hooked by `patch_camera_speed` | read |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84c18a88` | `View_vtable` | A view's vtable (the free camera checks a view by it) | tested |

## Structures

### The player camera

| Offset | Field | Sure |
|---|---|---|
| `+0x178` | The pitch limits (`{-85, 89}` in the field) | tested |
| `+0x18c` | The pitch, in degrees (rises as the view tilts down) | tested |
| `+0x1a4` | The yaw, in degrees (falls as the view turns right) | tested |
| `+0x204` | The state: 2 an explicit recentre, 3 following Link by itself, 4 the player's (the right stick) | tested |

## Notes

- In state 3 the camera eases the pitch back to its rest over a couple of seconds; in state 4 a pitch stays where it was
  put. Writing the state from outside doesn't stick: the game puts it back, and the angles with it, the next frame.
- State 3's yaw follow: after `f_80642c80`'s authored heading override, the block at 0x8064720c derives a heading from Link's
  movement and eases the yaw through `f_80642410`. `patch_camera_centering` skips only that movement path (restoring `x25`'s
  parameter base as that path does) and joins the other yaw paths at 0x80645ad8. State 4's stick, state 2's recentre and
  authored overrides stay; so do the pitch and the collision.
- The yaw and pitch writers were found with hardware write watchpoints on the two angles in Kokiri Forest.
