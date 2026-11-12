#!/bin/bash
# The harness's guard for the RTX 4090, which is also the user's desktop. One instance runs while anything of the
# harness may use the card (hs-gpu-slot starts it). It does two things.
#
# 1. A driver fault ends every harness run at once and closes the card. On 2026-11-10 at 12:08:48 the kernel logged
#    "NVRM: Xid ... 8" on a harness game; the game and a test program went on for another half minute, the driver's
#    watchdog reported the GPU locked, and Hyprland crashed into safe mode with every window the user had open. At the
#    first such kernel line this guard kills every harness process that has the card open (SIGKILL: a hung client does
#    not answer anything else), writes the file "closed" into the slot folder, which makes both gates refuse until the
#    main session removes it, writes what it saw to reports/GUARD-TRIPPED.txt and reports/LOG.md, and writes 1 into
#    the user's switch /home/mooki/Games/.hookshot-harness/gpu-slots (the gate then lets one game in at a time, not
#    two, until someone writes 2 again).
#
# 2. Nothing of the harness uses the card outside the gate. The likeliest cause of that crash was a test program built
#    in /tmp and run beside a game without the gate. Every second the guard looks at each process of this user
#    that has an NVIDIA device node open: one whose executable lies in a harness game copy (Hookshot's
#    .hookshot-h-*), a worktree or clone (Hookshot's and anything built under hookshot-wt/) or
#    /tmp, and that does not run inside the gate (the gate puts HSGATE_IN_GATE=<its process id> into the environment
#    of its command, which every child inherits; that process must be alive and hold the lock of one of the gate's
#    two slots, the files 0 and 1 of the slot folder: hold it, not only have the file open as every waiter has), is
#    killed and logged. The user's own programs and system tools (nvidia-smi, vulkaninfo, his games in
#    ~/Games/Ocarina, ~/Games/Ocarina-linux-next and ~/Games/Hookshot) are never touched.
#
# (2026-11-10, HsWorkbench: Hookshot's game copies, worktrees and clone added; the reports moved from the other port's
#    reports, now reference only, to Hookshot's /home/mooki/Code/hookshot-wt/reports: GUARD-TRIPPED.txt and lines in
#    LOG.md.)
# (2026-11-11, GateTwo: the gate has two slots; inside the gate is what holds slot 0's or slot 1's lock; a fault line
#    also puts the switch back to 1.)
#
# 3. A hold that lasts is said aloud. The rule is 8 minutes for one hold of the card; neither the gate nor this guard
#    ends a command for it. When the run that holds a slot (the gate's files holder.0 and holder.1 in the slot folder:
#    its process id, and the file's time is its start) has held it for more than 8 minutes, one line goes to
#    long-holds.log in the slot folder (time, pid, slot or slots, minutes, command), and again every 5 minutes while
#    it lasts. The main session's watchdog reads that file.
#
#   gpu-guard.sh            run in the foreground (the gate starts it detached)
#   gpu-guard.sh --status   say whether it runs and whether the card is closed
#   gpu-guard.sh --reopen   remove the "closed" file (main session, after telling the user what happened)
# HSGATE_GUARD_SOURCE=<file> makes it read that file's new lines instead of the kernel log (its own test); then
# HSGATE_GUARD_LONG="<s> <s>" sets the seconds before a hold's first line and between its lines (480 and 300).
set -u
slots=${HSGATE_SLOT_DIR:-/tmp/hs-gpu-slots}
reports=${HSGATE_GUARD_REPORTS:-/home/mooki/Code/hookshot-wt/reports}
lockfile=$slots/guard.lock
closed=$slots/closed
mkdir -p "$slots"
pat='NVRM: Xid|GPU is probably locked|kmemsysDoCacheOp'
# The RTX 4090's device nodes (the Intel device's nodes are another lane's and are left alone).
nv_render=$(basename "$(readlink -f /dev/dri/by-path/pci-0000:01:00.0-render 2>/dev/null)")
nv_card=$(basename "$(readlink -f /dev/dri/by-path/pci-0000:01:00.0-card 2>/dev/null)")
dev_pat="-> (/dev/nvidia[^ ]*|/dev/dri/${nv_render:-renderD128}|/dev/dri/${nv_card:-no-such-card})\$"

if [ -n "${1:-}" ] && [ "$slots" != /tmp/hs-gpu-slots ] && [ -z "${HSGATE_GUARD_SOURCE:-}" ]; then
    echo "gpu-guard: ignoring HSGATE_SLOT_DIR=$slots (not the harness's slot folder)" >&2
    slots=/tmp/hs-gpu-slots; lockfile=$slots/guard.lock; closed=$slots/closed
fi
switch=/home/mooki/Games/.hookshot-harness/gpu-slots
[ "$slots" = /tmp/hs-gpu-slots ] || switch=$slots/gpu-slots   # (its own test: the test folder's switch)
case "${1:-}" in
    --status)
        if flock -n "$lockfile" true 2>/dev/null; then echo "guard: not running"; else echo "guard: running"; fi
        if [ -e "$closed" ]; then echo "card: CLOSED: $(head -n 1 "$closed")"; else echo "card: open"; fi
        if [ -r "$switch" ] && [ "$(tr -d '[:space:]' < "$switch")" = 2 ]; then echo "slots: 2 (two games at once)"; else echo "slots: 1"; fi
        exit 0 ;;
    --reopen)
        rm -f "$closed"; echo "card: open"; exit 0 ;;
esac

# A guard on another slot folder would take the real gate's runs for programs outside the gate and kill them (it
# happened on 2026-11-10 at 17:02 from a shell that still had HSGATE_SLOT_DIR set by an old test: four runs lost).
# So a guard on a folder other than the default one runs only as its own test (HSGATE_GUARD_SOURCE), and every
# guard judges only processes of its own folder (card_clients).
default_slots=/tmp/hs-gpu-slots
if [ "$slots" != "$default_slots" ] && [ -z "${HSGATE_GUARD_SOURCE:-}" ]; then
    echo "gpu-guard: HSGATE_SLOT_DIR=$slots is not the harness's slot folder: not started (unset it)" >&2
    exit 2
fi
exec 6>"$lockfile"
flock -n 6 || exit 0   # one guard is enough

harness_exe() {   # the executable is something the harness built or an agent wrote
    case "$1" in
        /home/mooki/Games/Ocarina/*|/home/mooki/Games/Ocarina-linux-next/*|/home/mooki/Games/Hookshot/*) return 1 ;;   # the user's own games
        /home/mooki/Games/.hookshot-h-*|/home/mooki/Code/hookshot-wt/*|/home/mooki/Code/Hookshot/*) return 0 ;;
        /tmp/*|/var/tmp/*|/dev/shm/*) return 0 ;;
    esac
    return 1
}

# The process $1 holds the lock of the file $2: it has the file open, and that open file carries a lock (the "lock:"
# line of its fdinfo). A gate that still waits has both slot files open without a lock, and so does not count.
holds_lock() {
    local f line
    for f in "/proc/$1/fd"/*; do
        [ "$f" -ef "$2" ] || continue
        while IFS= read -r line; do
            case "$line" in lock:*FLOCK*) return 0 ;; esac
        done 2>/dev/null < "/proc/$1/fdinfo/${f##*/}"
    done
    return 1
}
holds_slot() { holds_lock "$1" "$slots/0" || holds_lock "$1" "$slots/1"; }   # slot 0's lock or slot 1's, nothing else

# Prints "pid exe gate" (gate: 1 when it runs inside the gate) for each process of this user that has an NVIDIA
# device node open and whose executable is the harness's.
card_clients() {
    local p exe gate holder theirs
    for p in /proc/[0-9]*; do
        [ -O "$p" ] || continue
        exe=$(readlink "$p/exe" 2>/dev/null) || continue
        exe=${exe% (deleted)}
        harness_exe "$exe" || continue
        # only processes of this guard's slot folder: the folder is in their environment, or nowhere (the default)
        theirs=$(tr '\0' '\n' < "$p/environ" 2>/dev/null | sed -n 's/^HSGATE_SLOT_DIR=//p' | head -n 1)
        [ "${theirs:-$default_slots}" = "$slots" ] || continue
        ls -l "$p/fd" 2>/dev/null | grep -q -E -- "$dev_pat" || continue
        gate=0
        holder=$(tr '\0' '\n' < "$p/environ" 2>/dev/null | sed -n 's/^HSGATE_IN_GATE=//p' | head -n 1)
        case "$holder" in
            ''|*[!0-9]*) ;;
            *) holds_slot "$holder" && gate=1 ;;
        esac
        echo "${p#/proc/} $exe $gate"
    done
}

note() { echo "$(date '+%F %T') gpu-guard: $*" >> "$slots/guard.log"; }

trip() {   # $1: the kernel line
    echo 1 > "$switch"   # at every fault line: one game at a time from now on
    [ -e "$closed" ] && return
    local when; when=$(date '+%F %T')
    printf '%s driver fault: %s\n' "$when" "$1" > "$closed"
    local killed=""
    while read -r pid exe gate; do
        kill -9 "$pid" 2>/dev/null && killed="$killed $pid:$(basename "$exe")"
    done < <(card_clients)
    # also whatever else runs inside the gate (its script, the run tool): the hold is over
    local p
    for p in /proc/[0-9]*; do
        [ -O "$p" ] || continue
        ls -l "$p/fd" 2>/dev/null | grep -q -F -e "-> $slots/0" -e "-> $slots/1" && [ "${p#/proc/}" != "$$" ] && kill -9 "${p#/proc/}" 2>/dev/null
    done
    {
        echo "The GPU guard closed the card at $when."
        echo "Kernel line: $1"
        echo "Killed (pid:program):${killed:- none had the card open}"
        echo "Both gates refuse until .hookshot-harness/gpu-guard.sh --reopen is run by the main session."
        echo "The switch $switch was set to 1: one game at a time until someone writes 2 into it again."
        echo "Kernel lines since then: journalctl -k --since '$when' | grep -E '$pat'"
    } > "$reports/GUARD-TRIPPED.txt"
    echo "- $(date +%H:%M) [gpu-guard] GPU GUARD TRIPPED at $when on the kernel line \"$1\": killed${killed:- nothing (no harness process had the card open)}; the card is closed (both gates refuse) until the main session reopens it, and the switch gpu-slots is back at 1. See GUARD-TRIPPED.txt." >> "$reports/LOG.md"
    note "tripped: $1; killed:$killed"
}

# 3. the holds that last (see the top)
long_first=480 long_again=300
if [ -n "${HSGATE_GUARD_SOURCE:-}" ] && [ -n "${HSGATE_GUARD_LONG:-}" ]; then read -r long_first long_again <<< "$HSGATE_GUARD_LONG"; fi
declare -A said=()
long_holds() {
    local s pid since now tenths mins cmd
    local -A which=() from=()
    printf -v now '%(%s)T' -1
    for s in 0 1; do
        pid=$(cat "$slots/holder.$s" 2>/dev/null) || continue
        case "$pid" in ''|*[!0-9]*) continue ;; esac
        holds_lock "$pid" "$slots/$s" || continue
        since=$(stat -c %Y "$slots/holder.$s" 2>/dev/null) || continue
        which[$pid]="${which[$pid]:-}${which[$pid]:+ and }$s"
        from[$pid]=$since
    done
    for pid in "${!said[@]}"; do [ -n "${which[$pid]:-}" ] || unset "said[$pid]"; done   # (that hold is over)
    for pid in "${!which[@]}"; do
        [ $((now - from[$pid])) -ge "$long_first" ] || continue
        [ -n "${said[$pid]:-}" ] && [ $((now - said[$pid])) -lt "$long_again" ] && continue
        said[$pid]=$now
        tenths=$(( (now - from[$pid]) / 6 ))
        printf -v mins '%d.%d' $((tenths / 10)) $((tenths % 10))
        cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-300)
        echo "$(date '+%F %T') pid $pid has held slot ${which[$pid]} for $mins minutes (the rule: 8): $cmd" >> "$slots/long-holds.log"
    done
}

note "started, pid $$"
# 1. the kernel log, from now on
( if [ -n "${HSGATE_GUARD_SOURCE:-}" ]; then tail -F -n 0 "$HSGATE_GUARD_SOURCE" 2>/dev/null; else journalctl -k -f -n 0 -o cat 2>/dev/null; fi |
  while IFS= read -r line; do
      case "$line" in
          *"NVRM: Xid"*|*"GPU is probably locked"*|*"kmemsysDoCacheOp"*) echo "$line" ;;
      esac
  done ) > "$slots/guard.fifo.$$" &
jpid=$!
trap 'kill $jpid 2>/dev/null; rm -f "$slots/guard.fifo.$$"' EXIT
seen=0
looked=0
# 2. the loop: new kernel lines, and harness programs on the card outside the gate
while :; do
    n=$(wc -l < "$slots/guard.fifo.$$" 2>/dev/null || echo 0)
    if [ "$n" -gt "$seen" ]; then
        line=$(sed -n "$((seen + 1))p" "$slots/guard.fifo.$$")
        seen=$n
        trip "$line"
    fi
    while read -r pid exe gate; do
        [ "$gate" = 1 ] && continue
        if kill -9 "$pid" 2>/dev/null; then
            note "killed $pid ($exe): it had the card open outside the gate"
            echo "- $(date +%H:%M) [gpu-guard] killed pid $pid ($exe): a harness program had the card open outside the gate. Every program that creates a device goes through hs-gpu-slot." >> "$reports/LOG.md"
        fi
    done < <(card_clients)
    printf -v now '%(%s)T' -1
    if [ $((now - looked)) -ge 5 ]; then looked=$now; long_holds; fi   # (by the clock: a pass takes seconds under load)
    sleep 1
done
