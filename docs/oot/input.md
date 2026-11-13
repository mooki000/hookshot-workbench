# Input: controllers, motion and rumble

How the game reads its controllers and turns them into actions: the `nn::hid` functions it imports, the engine's controller
manager (sead's, with a device for the Npads and one for a keyboard and mouse), the controllers that turn a pad's state into
buttons and sticks, the motion addon the aim reads, the player's virtual keys, and the two paths rumble takes. Looked at in
November 2026 by static analysis of the binary only (no run in the game), to set down what the game asks of `nn::hid` and
how Longshot answers.

**Longshot code:** [host/nn_hid.c](../../host/nn_hid.c) (the HID functions: the Npad, six-axis, vibration, touch screen),
[host/pad_sdl.c](../../host/pad_sdl.c) (the controllers through SDL, motion and rumble), [host/hos_motion.h](../../host/hos_motion.h)
(the six-axis state), [host/hd_rumble.h](../../host/hd_rumble.h) (HD rumble), [host/mouse_native.c](../../host/mouse_native.c)
(native mouse look), [host/oot_menu.c](../../host/oot_menu.c) (the menu mouse), [host/display.c](../../host/display.c) (the
mouse's capture), [nx2/standalone.py](../../nx2/standalone.py) (the stubs for the `nn::hid` functions the host does not
define), [nx2/oot_patches.py](../../nx2/oot_patches.py) (`patch_mouse_camera`, `patch_menu_focus`, `patch_menu_tick`)
**See also:** [camera.md](camera.md) (the stick's turns and the mouse's), [menus.md](menus.md) (the menu mouse),
[frame-time.md](frame-time.md) (the global step the hold timers advance by), [player.md](player.md),
[game-data.md](game-data.md) (`IsLastSelectedMQMode`), [jobs.md](jobs.md) (the calc tree)

## In short

1. **The engine's input is sead's.** A `sead::ControllerMgr` task (its instance at `0x84e9f680`) holds the devices and the
   controllers. Each calc it runs every device's calc, then every controller's (`f_8038a140`). **read**
2. **One device reads every Npad.** The Npad device (`f_834893d0`, 0x8788 bytes) asks `GetNpadStyleSet` for Npads No.1 to
   No.8 and Handheld, then reads the 16 newest states of each one's style with `GetNpadStates`, and the 16 newest six-axis
   states of each sensor. When a pad's style changes it fetches the six-axis and vibration handles again and starts them.
   **read**
3. **A controller per Npad, and one that merges them.** An Npad controller (`f_83b468d0`) turns its pad's newest state into
   sead's button bits and two sticks in -1..1; a merged controller (`f_83b47ea0`) ORs the buttons of all of them and takes the
   first stick that moves. The controller module's `+0xb0` is the merged controller: the camera's input reads it. **read**
4. **Hold timers run by time.** sead's per-button hold timers (and so its key repeat) advance by the global step
   (`0x84d61fc8`, [frame-time.md](frame-time.md)), not by one a calc, so they keep their timing above 60 fps. **read**
5. **In Master Quest mode the horizontal input is flipped.** The camera input's query (`f_805965e0`) negates both sticks' X
   and the motion values when `IsLastSelectedMQMode` (0x605e5df8) is set. **read** That points to a mirrored world in Master
   Quest, as on the 3DS. **guess**
6. **Rumble takes two paths.** The rumble module plays `.bnvib` files (`Rumble/Common.bnvib.sarc`) through the SDK's vibration
   node graph on its own timer thread (`rmbl::VibrationThread`); the Npad device has a `VibrationThread` of its own that sends
   queued values with `SendVibrationValue`. Longshot answers both at the `nn::hid` boundary. **read**

## What the game asks of nn::hid

The main module imports 61 `nn::hid` symbols (62 import slots; the stubs are at 0x83c61f80 to 0x83c64810), one of them
data (`VibrationNode::DefaultVibrationSampleInterval`). A free function of `nn::hid` that Longshot defines (`HLE(...)` in [host/nn_hid.c](../../host/nn_hid.c)) replaces the SDK's; one it does not
define gets a generated stub that logs its first call and returns 0 ([nx2/standalone.py](../../nx2/standalone.py)). The
vibration classes (`VibrationPlayer`, `VibrationMixer`, `VibrationWriter`, `VibrationTarget`, `VibrationNode`) and
`ControllerSupportArg::SetDefault` are class members: they run as lifted SDK code, which calls `nn::hid::detail` functions
that the host does define.

| Import | Where the game calls it | What Longshot answers |
|---|---|---|
| `InitializeNpad`, `SetSupportedNpadIdType`, `SetSupportedNpadStyleSet` | The Npad device's constructor (`f_80ed67c0`: ids No.1 to No.8 and Handheld, styles 0x1f: FullKey, Handheld, JoyDual, JoyLeft, JoyRight); the controller module's setup (`f_83b44500`); the style script node (`f_81000d20`) | Nothing to do |
| `GetNpadStyleSet` | The Npad device's calc, for each of the nine ids | FullKey (1) for No.1, 0 for the rest |
| `GetNpadStates` (FullKey, Handheld, JoyDual, JoyLeft, JoyRight) | The Npad device's calc: 16 states of the pad's style | The same state for every style: count copies, newest first, the sampling number one more each call; buttons, sticks and "connected" from every pad, the keyboard, the mouse, the input script and the menu mouse |
| `GetSixAxisSensorHandles` | The Npad device's calc, on a style change: 2 asked for | Writes the handles asked for and says 3 (the comment there: the aim's motion path) |
| `StartSixAxisSensor` | On a style change, each handle | Nothing to do |
| `SetGyroscopeZeroDriftMode` | On a style change, only when the device's `+0x8782` is not 0 | Stub (0) |
| `GetSixAxisSensorStates` | The Npad device's calc, 16 a sensor, while `+0x8781` is set | The pad's motion through SDL ([host/hos_motion.h](../../host/hos_motion.h)), or a pad lying still |
| `GetVibrationDeviceHandles`, `InitializeVibrationDevice` | On a style change | Two handles (left, right) |
| `SendVibrationValue` | The Npad device's `VibrationThread` (`f_83489ad0`) | To the pad's motors, or as HD rumble to a Switch controller (`vibration_send`) |
| `IsOunceVibrationDeviceMounted` | `f_830502a0` | Stub (0: not mounted) |
| `DisconnectNpad` | `f_81041ae0`, for ids 0 to 7 | Nothing to do |
| `SetNpadJoyHoldType` | `f_81000d20`, with the hold type it keeps at the device's `+0x2c` | Nothing to do |
| `ShowControllerSupport` | `f_83b45f80` and the controller module's update (`f_83b45780`) | One player, No.1 |
| `GetJcmsHandle`, `StartJcms`, `StopJcms`, `GetNmouseHandles`, `GetNmouseTypeSet`, `GetSensorFusionHandles` | The Npad device's calc, only while its Jcms, Nmouse or sensor-fusion buffer (`+0x8778`, `+0x8768`, `+0x8770`) is set | Stubs (0). The game imports no `InitializeJcms`, `InitializeNmouse` or `InitializeSensorFusion`, so these paths are likely never taken |
| `FinalizeJcms`, `FinalizeNmouse`, `FinalizeSensorFusion` | The Npad device's destructor, for the same buffers | Stubs (0) |
| `InitializeMouse`, `InitializeKeyboard` | The keyboard and mouse device's constructor (`f_83b486e0`) | Nothing to do |
| `GetMouseStates`, `GetKeyboardState` | The keyboard and mouse device's calc (`f_83b48780`) | Stubs (0): no states, and the buffers are not written (see *Notes*) |
| The vibration classes | The rumble module and its thread (`f_80c79650`, `f_83a68ff0`) | Lifted; their `nn::hid::detail` calls (`SendVibrationValue(s)`, `EnqueueVibrationValue`, `GetVibrationDeviceInfo`, the permit calls) are the host's |

The game does not import `GetTouchScreenStates`, the debug pad or the gesture functions. Longshot's touch-screen answer
(no touches) is for other games.

## The manager and the devices

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80f487d0` | `ControllerMgr_Ctor` | The controller manager's constructor (0x188 bytes, from the factory `f_80f48780`): a task named "sead::ControllerMgr" (vtable `0x84c189c0`), its device list at `+0x160` and its controllers at `+0x178`/`+0x180` | read |
| `f_80ed65d0` | `ControllerMgr_Setup` | Creates the manager's task (its factory `f_80f48780`), then the Npad device (0x8788 bytes, with the thread priority at `+0x330` of its caller) and adds it to the manager | read |
| `f_80ed66d0` | `ControllerMgr_AddDevice(mgr, device)` | Links a device into the manager's list (`+0x160`, the count at `+0x170`, the node's offset at `+0x174`) | read |
| `f_8038a140` | `ControllerMgr_Calc` | The manager's calc (its vtable `+0xb0`): every device's calc (`+0x20`), then every controller's calc (`+0x30`, called directly when it is `f_8038a220`) | read |
| `f_81041d30` | `ControllerMgr_NpadDevice(mgr)` | The first device in the list whose type is the Npad device's; 0 when there is none | read |
| `f_81041e10` | `ControllerMgr_Controller9` | The controller whose id (`+0x138`) is 9, under a mutex (`[0x84eb5f48]+0xd60`). Likely the merged controller | guess |
| `f_80ed67c0` | `NpadDevice_Ctor(device, mgr, heap, priority)` | Sets the device id 13 (`+0x18`), 8 Npads (`+0x28`), builds the `VibrationThread` at `+0x8518`, calls `InitializeNpad`, `SetSupportedNpadIdType` (the nine ids at `0x83f38fb8`) and `SetSupportedNpadStyleSet(0x1f)`, clears the nine records (style 15, none), puts the thread on core mask 2 and starts it | read |
| `f_834892b0` | `NpadDevice_Dtor` | Finalises Jcms, Nmouse and sensor fusion if their buffers are set, frees them, and stops the thread | read |
| `f_834893a0` | `NpadDevice_DtorDelete` | The deleting destructor (0x8788 bytes) | read |
| `f_834893d0` | `NpadDevice_Calc(device)` | Once a calc, for Npads No.1 to No.8 (records 0 to 7) and Handheld (0x20, record 8): `GetNpadStyleSet`, then `GetNpadStates(record, 16, id)` for the first style it has, in the order JoyDual, FullKey, JoyLeft, JoyRight, Handheld (style bits 12, 13 and 14 count as JoyDual, JoyLeft and JoyRight). Keeps the style's index at `+0x30 + 4n` (0 FullKey, 1 Handheld, 2 JoyDual, 3 JoyLeft, 4 JoyRight, 15 none). On a change it fetches the six-axis handles (2 asked for) and the vibration handles, sets the zero-drift mode from `+0x8782` (1 loose, 2 standard, 3 tight, 0 leave it), and starts each sensor and vibration device. While `+0x8781` is set it reads 16 six-axis states a sensor; from the third sensor on, it reads the first sensor's handle into the first buffer again | read |
| `f_80ed6970` | `VibrationThread_Ctor` | A sead thread named "VibrationThread" (vtable `0x84c16050`), a message queue of 15, a queue of 16 vibration commands at `+0xd8` and a critical section at `+0x230` | read |
| `f_83489ad0` | `VibrationThread_Calc` | Takes the oldest command (a handle and a `VibrationValue`, 20 bytes) off the queue under the lock (`f_83489b50`) and sends it with `nn::hid::SendVibrationValue` | read |
| `f_83489b50` | `VibrationQueue_Pop(queue, out)` | Pops one 20-byte entry from the ring (`+0` storage, `+8` capacity, `+0xc` read index, `+0x10` count) | read |
| `f_83b486e0` | `KeyMouseDevice_Ctor(device, mgr)` | The keyboard and mouse device (vtable `0x84c81cf8`, device id 10): clears its key bits and counters, calls `InitializeMouse` and `InitializeKeyboard` | read |
| `f_83b48780` | `KeyMouseDevice_Calc(device)` | Reads 16 mouse states and the keyboard's state, and keeps hold, trigger, release and repeat bits for 256 Windows virtual-key codes, from the table at `0x83f8f8a8` (131 entries: codes 1, 2, 4, 5 and 6 are the mouse's buttons, the rest a keyboard key). A key held for 30 calcs or more sets its repeat bit each calc (a count of calcs, not of time). Also the pointer (pixels at `+0x1a8`, -1 to 1 at `+0x1b0`) and the wheel (`+0x1c0`). It stops early when bit 0 of the newest mouse state's attributes is set | read |

## The controllers

The controllers are sead's: `+0x114` the buttons held, `+8` the ones pressed this calc, `+0xc` released, `+0x10` repeated,
`+0x120` and `+0x128` the left and right sticks (*Structures*). The button bits are sead's pad indices; the mapping from
`nn::hid`'s is in *Notes*.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_8038a220` | `Controller_Calc(controller)` | sead's controller calc, shared by every controller class (their vtables' `+0x30`): keeps the last hold (`+0x13c`), runs the class's calc (`+0x38`), turns the sticks into direction bits with hold and release thresholds (`+0xf0` to `+0xfc`), works out the pressed and released bits, advances each held bit's timer (`+0x30 + 4n`) by the global step and sets its repeat bit from its delay and pulse (`+0xb0`, `+0xd0`), then runs the addons (`+0x148`, the motion addon inlined) | read |
| `f_83b468d0` | `NpadController_CalcImpl(controller)` | Finds the Npad device, takes its pad (`+0x178`, 0 to 8) and that pad's style; on a change it resets its two addons and the last sampling number. Reads the newest state: the connected bit from the attributes, the buttons mapped to sead's bits, the sticks times 1/32767 (`0x83e6085c`) and cut to length 1. JoyLeft and JoyRight alone have their own mappings and one stick, turned for the hold type (`device+0x2c`). With `+0x17c` bit 3 set it ORs the buttons of every state newer than the last one it saw | read |
| `f_83b468c0` | `NpadController_IsConnected` | Bit 0 of `+0x17c` | read |
| `f_83b47ea0` | `MergedController_CalcImpl(controller)` | Over its controllers (`+0x188`, the count at `+0x180`): ORs their buttons, takes the first non-zero left stick, right stick and triggers, and the pointer; connected (`+0x178`) when any of them is | read |
| `f_83b48180` | `MergedController_IsConnected` | `+0x178` | read |
| `f_83b479c0` | `MotionAddon_Calc(addon)` | The six-axis addon of an Npad controller (`addon+0x20`): when the pad is connected and its style is set, copies the new six-axis states (the sampling number's advance on `+0x528`, at most 16, the count at `+0x530`) into 80-byte samples at `+0x28`, with axes turned into the game's frame (another turn for a JoyRight held sideways). It reads the second sensor when the device reports exactly two (`+0x534` chooses) and the first otherwise. A sample with no acceleration or an identity orientation counts as no data (`+0x535`) | read |

## The controller module and the applet

The engine's controller module (vtable `0x84c818e8`, its instance at `[0x84eb2f10]`) owns the controllers. Its `+0xb0` is the
controller the camera's input reads.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80fb5b70` | `ControllerModule_Create(heap)` | Creates the module (0x1a0 bytes) once, among the engine's modules (`f_80fb5200`), and keeps it at `0x84eb2f10` | read |
| `f_83b44500` | `ControllerModule_Init` | The module's setup (its vtable `+0x38`): creates the controllers and their addons and the keyboard and mouse device (`f_83b486e0`), links eight controllers through `f_810421f0`, and calls `SetSupportedNpadIdType` | read |
| `f_83b45780` | `ControllerModule_Update` | The module's vtable `+0x10`: under a try-lock, checks the pads and, when its state asks for it, calls `ShowControllerSupport` | guess |
| `f_83b45f80` | `ControllerModule_ShowControllerSupport` | Fills a `ControllerSupportArg` (`SetDefault`), shows the applet under the module's locks and assigns the pads from its result (`f_81041e90`). `SeqExecuteShowControllerSupportApplet` calls it | read |
| `f_81041e90` | `Controllers_AssignNpads` | Finds the Npad device and controller 9 and hands the Npads to the controllers, disconnecting the ones it does not use | guess |
| `f_81041ae0` | `Npad_Disconnect(_, id)` | `DisconnectNpad` for an id up to 7 | read |
| `f_81000d20` | `ControllerModule_SetStyles(module, styles, hold)` | `SetSupportedNpadStyleSet(styles)`, keeps the hold type at the Npad device's `+0x2c` and calls `SetNpadJoyHoldType` | read |
| `f_81000ca0` | `SeqOneShotSetSupportControllerStyle_Eval` | The script node: five bools (the five styles) and a hold type, to `f_81000d20` | read |
| `f_830502a0` | `Npad_IsOunceVibrationMounted` | Asks `IsOunceVibrationDeviceMounted` for the active pad's vibration handle (`+0x284` the count). "Ounce" is likely a Switch 2 controller | guess |

## Virtual keys and the player's input

The player's actions read "virtual keys", not the controller: 64-bit masks the queries read by bit. The blackboard names are
`PlayerVirtualKey`, `PlayerVirtualKeyHold` and `PlayerVirtualKeyRelease` (their name pointers in the table at `0x84c9fd38`),
next to `PreRawLeftStickRadian`, `ReverseInput` and `RawLeftStick`. Script nodes also buffer early presses ("pre-input":
`ExecutePlayerCalcPreInput`, `OneshotPlayerResetVirtualKeyTrigAttackWithPreInput`, `UseItemTriggerPreInputTime`).

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_82ae61c0` | `QueryPlayerIsVirtualKeyTrig_Eval` | The query `QueryPlayerIsVirtualKeyTrig` (vtable `0x84aff220`): its input `VirtualKey`, its output `IsTrig`, from `f_80955090` on the node's actor's virtual keys | read |
| `f_82ae6030` | `QueryPlayerIsVirtualKeyHold_Eval` | The same for `QueryPlayerIsVirtualKeyHold` (vtable `0x84aff0d0`), through `f_80993500` | read |
| `f_80955090` | `VirtualKey_IsTrig(keys, n)` | Bit n of the 64-bit mask `keys+0x40` points to | read |
| `f_80993500` | `VirtualKey_IsHold(keys, n)` | Bit n of the mask `keys+0x50` points to | read |
| `f_80964e50` | `Actor_VirtualKeys(actor)` | `[actor+0x270]+0x358`, or 0 | read |
| `f_80853d60` | `Node_Actor(node)` | A script node's actor: `[[node+8]+8]+0x1f0` | read |
| `f_80954390` | `Player_StickMove` | The player's movement from the stick ("Move", "Turn", "Brake", "LockOnMove", "PlayerMoveSpiderWeb"), with `atan2f` of the stick and two trigger checks through `f_80955090` | guess |
| `f_805965e0` | `QueryPlayerCameraControllerInput_Eval` | The camera's input (vtable `0x84afc550`): from the controller module's `+0xb0`, the right stick, the left stick, the buttons held and pressed; then the motion addon's sample count and values. In Master Quest mode (`IsLastSelectedMQMode`) it negates both sticks' X and six of the motion values. Its output is `PlayerCameraControllerInput` | read |
| `f_828a9190` | `OcarinaWait_Start` | The ocarina's note-wait action's start; the wait's kind is its `+32`: 1 and 3 the field ocarina, 2 a song lesson, 10 the scarecrow's recording, 13 a dialogue's. Hooked by `patch_ocarina_wait`: reports the wait to the host (`hos_oc_wait`, `hos_oc_beat`), which swaps the five notes for its own controls (`host/nn_hid.c`); a dialogue's wait stays the game's | read |
| `f_828a9280` | `OcarinaWait_Tick` | The action's tick: while it runs it re-arms the wait, behind three gates (its mode check, the input context `f_806aac40`, the wait's owner `f_82856760`). Hooked by `patch_ocarina_wait`: a gate that refuses reports no wait, so a dialogue keeps its buttons | read |
| `f_832b35d0` | `OcarinaInput_Reset` | The note input's reset between note windows, called by the start and the tick; after it, that window's wait is over | read |
| `f_806aac40` | `OcarinaWait_InputContext` | The tick's check that its input context still exists | read |
| `f_82856760` | `OcarinaWait_Owner` | The tick's check that the wait's owner still asks for notes | read |

## Rumble

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80c79650` | `RumbleModule_Init` | "engine::rumble::RumbleModule": loads `Rumble/Common.bnvib.sarc` and starts its vibration thread (`f_80c7a080`) | read |
| `f_80c7a080` | `RumbleModule_StartThread` | Starts "rmbl::VibrationThread", a sead thread running `f_83a68ff0`, on the core its parameter names, with a periodic timer event of its parameter's period | read |
| `f_83a68ff0` | `RumbleThread_Loop` | Waits for the timer, then under the lock calls `nn::hid::VibrationNode::Update` (the SDK's graph: player, mixer, writer, target) and `f_80a65500` | read |
| `f_80707730` | `Rumble_BindTargets` | Finds the active pad's record and binds the rumble's `VibrationTarget`s to its vibration handles (`f_83a69180`, `f_83a69320`), under a mutex | guess |
| `f_83a69180` | `RumbleTarget_Bind` | Unsets and sets a `VibrationTarget`'s device handle and reconnects it to its source | read |
| `f_828e2fa0` | `OneShotControllerRumble_Eval` | The script node `OneShotControllerRumble` (vtable `0x84a18708`) | guess |
| `f_80807530` | `SeqOneShotSetControllerRumbleIntensity_Eval` | The script node `SeqOneShotSetControllerRumbleIntensity` (vtable `0x84bd9e98`); it reads the game-data manager | guess |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84e9f680` | `g_ControllerMgr` | The `sead::ControllerMgr` instance (`[0x84e9f680]`; the GOT word `0x84ca1b48` points here) | read |
| `0x84c189c0` | `ControllerMgr_vtable` | The manager's vtable (its calc at `+0xb0`) | read |
| `0x84c16018` | `NpadDevice_vtable` | The Npad device's vtable (its calc at `+0x20`) | read |
| `0x84c16050` | `VibrationThread_vtable` | The Npad device's vibration thread's vtable (its calc at `+0x70`) | read |
| `0x84c81b00` | `NpadController_vtable` | The Npad controller's vtable (`+0x20` connected, `+0x30` calc, `+0x38` its own calc) | read |
| `0x84c81b98` | `MergedController_vtable` | The merged controller's vtable | read |
| `0x84c81b60` | `MotionAddon_vtable` | The motion addon's vtable (its calc at `+0x20`) | read |
| `0x84c81cf8` | `KeyMouseDevice_vtable` | The keyboard and mouse device's vtable (its calc at `+0x20`) | read |
| `0x84c818e8` | `ControllerModule_vtable` | The controller module's vtable | read |
| `0x84eb2f10` | `g_ControllerModule` | The controller module; `[0x84c9ef30]` points here | read |
| `0x83f38fb8` | `NpadIdTable` | The nine Npad ids the game supports: 0 to 7 and 0x20 (Handheld) | read |
| `0x83f8f8a8` | `KeyMouseTable` | 131 pairs (Windows virtual-key code, mouse button or keyboard key) | read |
| `0x83e6085c` | `StickScale` | 1/32767, a stick's integer to -1..1 | read |

## Structures

### The Npad device (0x8788 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0x18` | The device id (13) | read |
| `+0x20` | The controller manager | read |
| `+0x28` | The number of Npads before Handheld (8) | read |
| `+0x2c` | The Joy-Con hold type (`f_81000d20`) | read |
| `+0x30 + 4n` | Record n's style index (0 FullKey, 1 Handheld, 2 JoyDual, 3 JoyLeft, 4 JoyRight, 15 none) | read |
| `+0x58 + 0xec0 n` | The nine records (below): Npads No.1 to No.8, then Handheld | read |
| `+0x8518` | The `VibrationThread` | read |
| `+0x8748` | The thread's critical section | read |
| `+0x8768`, `+0x8770`, `+0x8778` | The Nmouse, sensor-fusion and Jcms buffers (0 unless set) | read |
| `+0x8780`, `+0x8781`, `+0x8782` | Jcms wanted (1), six-axis on (1), the zero-drift mode to set (0) | read |

### An Npad record (0xec0 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0` | 16 Npad states, 0x28 bytes each, newest first (`+0` sampling, `+8` buttons, `+0x10` sticks, `+0x20` attributes) | read |
| `+0x280` | The six-axis handle count | read |
| `+0x284` | The vibration handle count | read |
| `+0x288`, `+0x289`, `+0x28a` | Jcms started; the Nmouse and sensor-fusion handle counts | read |
| `+0x28c`, `+0x290` | The two six-axis handles | read |
| `+0x298`, `+0x898` | 16 six-axis states (0x60 each) of the first and second sensor | read |
| `+0xe98`, `+0xe9c` | The two vibration handles | read |
| `+0xea0`, `+0xea8`, `+0xeb8` | The Jcms, Nmouse and sensor-fusion handles | read |

### A controller (sead's, as the calc uses it)

| Offset | Field | Sure |
|---|---|---|
| `+8`, `+0xc`, `+0x10` | Buttons pressed, released and repeated this calc | read |
| `+0x14` | Pointer flags | read |
| `+0x30 + 4n` | Bit n's hold timer, a float the global step is added to | read |
| `+0xb0 + n`, `+0xd0 + n` | Bit n's repeat delay and pulse | read |
| `+0xf0` to `+0xfc` | The sticks' hold and release thresholds | read |
| `+0x104`, `+0x108` | The first of the left and right sticks' four direction bits | read |
| `+0x114` | Buttons held | read |
| `+0x118` | The pointer | read |
| `+0x120`, `+0x128` | The left and right sticks (x, y in -1..1) | read |
| `+0x130`, `+0x134` | The analogue triggers | read |
| `+0x138` | The controller's id | read |
| `+0x13c` | Last calc's buttons held | read |
| `+0x140` | The controller manager | read |
| `+0x148` | The addons' list | read |

An Npad controller adds `+0x178` its pad (0 to 8), `+0x17c` flags (bit 0 connected, bit 3 merge the history), `+0x17d` the
last style and `+0x180` the last sampling number seen. A merged controller has its controllers at `+0x188` (the count at
`+0x180`) and its connected flag at `+0x178`. The motion addon has its controller at `+0x20`, 16 samples of 80 bytes at
`+0x28`, the last sampling number at `+0x528`, the new samples' count at `+0x530` and its flags at `+0x534` to `+0x538`.

## Notes

- [ai.md](ai.md) reads `+0x440` of `[0x84eb5f48]` as a frame counter (`f_801c5ef0`): this page's name for `+0xd60` of that object (the controller manager's mutex) may need checking. **guess**

- **The button mapping.** For FullKey, Handheld and JoyDual, `nn::hid`'s buttons become sead's pad bits: A 0, B 1, ZL 2, X 3,
  Y 4, ZR 5, right-stick click 6, left-stick click 7, Minus 9 and 12, Plus 10 and 11, L 13, R 14, d-pad up 16, down 17,
  left 18, right 19. The stick directions are sead's own bits, worked out from the sticks in `f_8038a220`. **read**
- **Where a frame's input is read.** `ControllerMgr_Calc` reads every device in one go, so the Npads are sampled once a
  calc, by `GetNpadStates`. The manager is a sead calc task, so the calc likely runs in the framework's calc tree on the main
  thread ([jobs.md](jobs.md)); where it sits against the module phase was not traced. **guess** Rumble runs on its own
  threads: "rmbl::VibrationThread" on a timer, and the Npad device's "VibrationThread" on core mask 2. **read**
- **What Longshot gives the game.** One Pro Controller on No.1, whatever the player uses, so record 0 with style FullKey
  is the only one the game sees. The buttons and sticks are the sum of every pad (SDL, or XInput when SDL has none), the
  keyboard, the mouse (as the right stick, or nothing when native mouse look takes it), `HOS_INPUT_SCRIPT` and
  `HOS_INPUT_LIVE`, and the menu mouse's taps and sticks (`hos_hid_tap`, `hos_hid_stick`, `hos_hid_drag`). Each call
  returns the same state count times, so the controller's history merge (`+0x17c` bit 3) finds one new state a calc.
- **Native mouse look** turns the player camera's yaw and pitch in `f_80644c80` before the camera reads its input
  ([camera.md](camera.md)); the stick path above is left alone while it does. The menu mouse works through the Npad (taps
  and sticks) and the menu focus ([menus.md](menus.md)).
- **The keyboard and mouse device reads stale memory under Longshot.** `GetMouseStates` and `GetKeyboardState` are
  generated stubs: they return 0 and write nothing. `f_83b48780` still reads the newest mouse state's attribute byte and,
  when its bit 0 is clear, the keyboard state's key bits and the pointer, all from its own stack buffers, which nothing has
  written. Its key bits may then follow whatever was on the stack. Nothing traced here reads this device; whether anything
  does is open. **read**
- **The six-axis count.** The device keeps whatever count `GetSixAxisSensorHandles` returns: Longshot's 3 makes its calc read
  the first sensor twice (the third read goes into the first buffer), and the motion addon then reads the first sensor,
  where a count of exactly 2 would make it pick one with `+0x534`. Where a count of 3 matters for the aim, as the host's
  comment says, was not found. **read**
- **Options.** The input options (camera inversion, sensitivity, motion aim, rumble) are not named in the binary: the option
  screens (`UISystemOptionScreen`, `LayoutMsg/SystemOption_00`, `UI/OptionSetTable/OptionSetTable.game__ui__OptionSetTable.bgyml`)
  and their values live in the game's data, and game data is read by hash ([game-data.md](game-data.md)). The camera's
  parameters name the stick and the gyro separately (`LatStickScale`, `LngStickScale`, `LatGyroScale`, `LngGyroScale`,
  `GyroScaleWithFovyMin`, `GyroScaleWithFovyMax`), and a shooter's has `GyroDegreePerFrame` and `IsActiveGyroRotation`.
  **guess**
- **Windows.** The engine has a keyboard and mouse device keyed by Windows virtual-key codes, and a script node
  `SeqOneShotSetWinControllerActiveWindowsOnly`. **guess** (names and the key table only)

## Open questions

- Who writes the virtual-key masks (`keys+0x40`, `+0x50`, and likely a release mask), and from which controller: the
  controller module's `+0xb0`, or a controller of the player's own?
- Who pushes commands onto the Npad device's `VibrationThread` queue, and what sends that thread its messages?
- Is `+0x8782` (the zero-drift mode) ever set, and is `+0x8781` (six-axis on) ever cleared?
- Does anything read the keyboard and mouse device (id 10)? If so, the stubs' unwritten buffers matter (*Notes*).
- Where the game's aim needs a six-axis count of 3, as host/nn_hid.c says it does (issue 112).
- Which option values flip the camera or scale it, and where the camera applies them: the option names are hashes in the
  game's data.
- Whether the Master Quest flip in `f_805965e0` comes with a mirrored picture (a mirrored world), or only flips the input.
- When the manager's calc runs in the frame against the module phase, and on which thread exactly.
