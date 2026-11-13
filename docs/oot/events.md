# Events and the world

How code asks the game to play an event (a scripted sequence such as a change of map), and how the world's clock is set.
Looked at for the debug tools' teleport and time of day.

**LS code:** `host/hos_main.c` (`oot_teleport`, `oot_event_arg`, `oot_set_time`)
**See also:** [game-data.md](game-data.md) (the flags the teleport checks first), [player.md](player.md)

## Requesting an event

A request is built in an EventRequestArg, which names the event and carries its arguments, then handed to the event manager.
The teleport asks for `Dm_SY_BancChangeGo` with the arguments NextBanc, NextStartPos, BootEventName and IsCheckAir, and Link
as its starter, as SeqOneShotRequestEvent and OneShotEventMemberRequestEventByEventName do.

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_80d99f10` | `EventRequestArg_Init(arg)` | Makes an empty EventRequestArg in place | tested |
| `f_829df380` | `QueryBancChangeEventRequestArg` | Fills an EventRequestArg's arguments for a change of map (the entries' layout below). Builds the next map's name, NextBanc, as described in [Notes](#notes) under *The next map's name*: the base name by Link's age, "_MQ" in Master Quest mode, "_Alternative" by a game-data bool | read |
| `f_802dc440` | `ActorLink_Set(link, actor, 0)` | Points an actor link at an actor (the request's starter at `arg+0x1950`) | tested |
| `f_80d9a820` | `EventMgr_Request(events, arg)` | Asks the event manager to play the event the arg describes; the result is in `x0`. The event reads its string arguments after this returns, so they must stay in memory | tested |
| `f_8022b6f0` | `ActorLink_Release(link)` | Releases an actor link. After a request, SeqOneShotRequestEvent stores `0x84994b20` in each of the arg's two links and releases them | tested |
| `f_83b5bfb0` | `Opening_Timeline` | The new-game opening's timeline, which starts its two movies. Hooked by `patch_opening`: while the opening is skipped (`hos_oot_opening`) the movies are not started | read |
| `f_80de6cb0` | `Opening_SkipQuery` | The opening's skip query. Hooked by `patch_opening`: answered yes while the opening is skipped | read |

## The world's clock

| Function | Name | What it does | Sure |
|---|---|---|---|
| `f_834722d0` | `WorldMgr_SetTime(world, seconds)` | ExecuteWmSetTime's call: sets the world manager's time of day, in seconds since midnight. The world manager is `[[0x84c9e898]]`, and `world+0x510` must be set | tested |

## Data

| Address | Name | What it is | Sure |
|---|---|---|---|
| `0x84c9e090` | `g_EventMgrHolder` | The event manager, `[[0x84c9e090]]` (as SeqOneShotRequestEvent loads it) | tested |
| `0x84c9e898` | `g_WorldMgrHolder` | The world manager, `[[0x84c9e898]]` | tested |
| `0x84994b20` | `ActorLink_Empty` | What SeqOneShotRequestEvent stores in an actor link before releasing it (likely an empty link's vtable) | guess |

## Structures

### The event manager

| Offset | Field | Sure |
|---|---|---|
| `+104` | Not 0 while an event plays (the Main sequence's "Event" test) | tested |

### An EventRequestArg

| Offset | Field | Sure |
|---|---|---|
| `+0` | The arguments' capacity | tested |
| `+8` | The arguments: 200-byte entries | tested |
| `+16` | The number of arguments | tested |
| `+0x1950` | The starter actor's link | tested |
| `+0x1968` | Another actor link (released after the request) | tested |
| `+6800` | The event name's buffer | tested |
| `+6808` | Its capacity | tested |

### An event argument (200 bytes)

| Offset | Field | Sure |
|---|---|---|
| `+0` | The type: 1 a string pointer, 4 a bool | tested |
| `+8` | The name's buffer | tested |
| `+16` | Its capacity | tested |
| `+152` | The value (a string value is a pointer the request does not copy) | tested |

## Notes

- `0x84c9e898` holds the address of the world manager's variable `0x84e9eab0`; `world+0x510` is its clock (`Clock_SetTime`, `f_80f0e640`). See [environment.md](environment.md). **read**
### The next map's name

How `f_829df380` builds NextBanc, in order. **read**

1. **The base name:** NextBancParamNameChild or NextBancParamNameAdult, by `Sequence_IsChildhood` (0xcea220d3), into its
   name buffer (`+6976`, its length `+6984`).
2. **No "_MQ" for these:** a name that contains "_Child" or "_Adult" (an inline substring search), or that is
   FinalBattleField, HyruleCastle or GanonCastle, goes on to step 4. Those are the overworld maps with an age variant and
   the three castle maps.
3. **"_MQ":** it looks `IsLastSelectedMQMode` (0x605e5df8) up in the game-data manager (`[[0x84c9e180]]`,
   [game-data.md](game-data.md)): first in the bool table (the hash index at `+32`, its size at `+44`; the entries at `+24`,
   0x18 bytes each, their count at `+16`; the value at `+22`), then in a second table (the index at `+128`, size `+140`;
   64-byte entries at `+120`, read through their vtable's `+32`). When the bool is 1 it appends "_MQ" (`0x83d0488a`, with
   `f_8572f020`); not found or 0, nothing.
4. **"_Alternative":** appended when a local flag is 1; the flag is a game-data bool named by the IsAlternativeGameDataBool
   argument (hashed with `f_801939e0`, read with `f_800b2720`). The two alternative maps are the burned Kakariko and the
   thawed Zora's Domain, with the bools `Sequence_IsAlternative_KakarikoAdult` and `Sequence_IsAlternative_ZoraDomainAdult`
   ([maps.md](maps.md), *Variant maps*).
5. **NextBanc** is the result. NextStartPos goes the same way, with "_Air" for NextStartPosAir (IsCheckAir).

Step 3 doesn't check a list of dungeons (unlike `QueryGameBancParamMQName`, [master-quest.md](master-quest.md)): in Master
Quest mode any next map without an age suffix, other than the three castle maps, gets "_MQ". Whether boss rooms or other
age-less maps are ever reached through this query was not checked. **read** (the code), open (the maps it is used for)

- The teleport waits while an event plays (`events+104`) and while a map loads (`Sequence_IsLoadingBanc`), and picks the
  child or adult map by `Sequence_IsChildhood` ([game-data.md](game-data.md#known-values)).
