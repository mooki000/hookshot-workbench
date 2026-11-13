#!/bin/bash
# One unattended HS or LS run on the RTX 4090 (or, with --intel, the Intel UHD 770), through its gate. Written
# 2026-11-10 (HsWorkbench); --window 2026-11-11 (WindowRuns).
#
#   hs-run.sh <copy> <tag> <seconds> [--window] [--exclusive] [--whole] [--priority] [--intel] [--from <s>]
#             [--input '<presses>'] [--user <folder>] [--keep-user] [--prepare] [--perf] [--mem-max <v>] [NAME=VALUE ...]
#
#   <copy>      a built game folder /home/mooki/Games/.hookshot-h-<name> (hookshot OR longshot, image.bin, romfs/)
#   <tag>       the run's name: everything goes to <copy>/runs/<tag>/ (replaced if it exists)
#   <seconds>   wall time from the game's start; then SIGTERM (SIGKILL 20 s later). Exit 124 = ended at the wall
#   --window    a real window on the user's Hyprland instead of a headless run: for what needs a swapchain and the
#               compositor (presentation, frame pacing, V-Sync, frame generation's output, the window's title). ONLY
#               while the file /home/mooki/Games/.hookshot-harness/window-ok exists (the user's switch: the main
#               session creates it while he is away and removes it when he is back); see "With --window" below.
#               Not with --intel, --prepare or HOS_HEADLESS
#   --exclusive the gate's exclusive hold (no build beside it, no other game on the card): for frame-rate measurements only
#   --whole     the whole card for this run (both of the gate's slots: no other game beside it), without stopping the
#               builds. Added by itself when the run's picture is larger than the game's own 2048x1152 (see
#               picture_large below); ask for it when a run needs the card's memory for another reason
#   --priority  the gate serves this run before the waiters that asked without it. ONLY for runs the main session has
#               named (the co-op and randomizer lanes' short runs, the performance holds): never to jump a queue
#   --intel     the Intel device instead: its gate (hs-intel-slot), Mesa's ICD only (intel_icd.json) and
#               RX_VK_DEVICE=0 = the UHD 770; the kernel count is then i915/xe hang, reset and error lines
#   --from <s>  frame statistics only from <s> seconds after the first present on (skip title, load, warm-up)
#   --input     HOS_INPUT_SCRIPT, e.g. '20:A,25:A,30:PLUS' (docs/development.md: Input for scripted runs); with it
#               HOS_INPUT_EXCLUSIVE=1, so only the script drives the game
#   --user      the save folder to start from (either game's --user folder: save/<user id>/...); copied, never used in
#               place. Default: <copy>/user-template if it exists, else an empty folder (a fresh profile)
#   --keep-user keep the run's throw-away user folder (default: only its save/ is kept, for a look afterwards)
#   --prepare   run the detected executable with --prepare-shaders (what a build's last step and a first start do:
#               the shader list translated and the shipped pipelines built, into the copy's caches), same environment
#   --mem-max <v> the game in a transient systemd user scope with MemoryMax=<v> and no swap (e.g. 28G): the kernel
#               ends it there instead of the machine running out (Intel's driver took 23-28 GB preparing shaders,
#               issues 999/1001/775)
#   --perf      optional sampled system context in runs/<tag>/perf.csv; no change to summary statistics
#               (older builds may write no file). Uses the run folder and all fields, after NAME=VALUE.
#   NAME=VALUE  more of the game's switches for this run (over the defaults below)
#
# The run: headless (HOS_HEADLESS=1: no window, no Wayland or X11 connection: WAYLAND_DISPLAY and DISPLAY unset),
# muted (HOS_AUDIO=0, SDL's dummy sound driver), NVIDIA's Vulkan driver only (VK_DRIVER_FILES) and RX_VK_DEVICE=0 =
# the RTX 4090 (the log's device list is checked after the run), no update check, no Discord status, no physical
# controller in the background, an empty HOS_ENV_FILE (the copy's hookshot_env.txt/longshot_env.txt is not read). The driver's
# shader caches stay in the copy (.nv-cache, .mesa-cache, .xdg-cache), XDG_DATA_HOME is the run's folder: nothing is
# written to the user's ~/.local/share, ~/.cache or ~/.config. Pictures every RX_VK_SHOT_EVERY presents (300) at
# 1280x720 into runs/<tag>/shots, the log into runs/<tag>/<project>_log.txt, and every game present's time into
# runs/<tag>/present.csv (HOS_PRESENT_TRACE, host/nn_vi.c: the game's own presents, frame limiter included, CPU
# clock). A populated RX_VK_FRAME_LOG (runs/<tag>/frames.csv) supplies renderer intervals: headless-renderer after
# offscreen submission, or swapchain with --window. Otherwise present.csv supplies game intervals (three or seven columns).
#
# With --window (the user's word of 2026-11-11, for exactly this): inside the gate, right before the game starts,
# hs-window.py finds the one live Hyprland and sends it two window rules for the class ls-harness (never
# focused, floating at the monitor's size on the visible workspace, the game's requests for fullscreen, maximize and
# activation ignored); the game starts only if the compositor answers `ok` to both, with SDL_APP_ID=ls-harness
# (SDL then names the window so: reports/WindowRuns.md), on Wayland only (DISPLAY unset), still muted. The window
# covers the monitor (1536x864 logical = 1920x1080 pixels at his scale of 1.25) and cannot be focused: keys, mouse
# and controllers do not reach it; HOS_INPUT_SCRIPT and HOS_INPUT_LIVE do. While it runs hs-window.py reads the
# clients and the active window and ends the run at once if the window has another class, is not floating, is
# fullscreen, is on another workspace or has the focus (runs/<tag>/window-problem.txt). The active window and the
# clients before and after are in runs/<tag>/window-before.json and window-after.json, the rules and their answers in
# window-rules.txt. One windowed run at a time (window.lock). RX_VK_FRAME_LOG is written now, so the frame
# statistics are the swapchain's presents (generated frames included) and game= is the game's own rate from
# present.csv over the same last seconds (the two files end together but start apart: the renderer's first present
# is the start-up screen's, the game's comes some seconds later, 8.8 s in the first run). The window is asked for at
# the monitor's logical size (SDL takes HOS_WINDOW_SIZE in logical units on Wayland: 1920x1080 gave a first swapchain
# of 2400x1350 that the rule then shrank). The summary adds: swapchain=<size>,<present mode>,vsync-<row>,x<times made>, game=<fps>, and
# hs-window.py's line (window=ok|none|PROBLEM(...) class= size= at= ws= float= active=same|CHANGED others=same
# screen=on|LOCKED(k/n)|BLANKED(k/n)). A run whose window= is not ok or whose active= is not same is disturbed: tell
# the main session. A run whose screen= is not on was not composited the whole time: its frame log is not a
# presentation measurement (no run starts with the screen locked or the monitor off; nothing ever wakes or unlocks it).
# The switch is read when the run is asked and again when its turn comes; a run in progress finishes when it goes.
#
# Prints one line (also runs/<tag>/summary.txt and appended to /home/mooki/Games/.hookshot-harness/runs.log):
#   exit, wall seconds, span (seconds from the first present to the last), frames (and their source), mean fps and 1%
#   low fps (1000 / the mean of the slowest 1% frame times), frames over 16.7, 20 and 25 ms (at the 60 cap the game's
#   intervals scatter around 16.67 ms, so over16.7 counts about half of them; over20 is the dropped-frame count), pictures (count, size of one,
#   MB), FATAL, CRASH, device-lost and "ran without a core" lines in the log, driver lines from start through end
#   (kernel=: faults/unknown signatures, or Intel hang/reset/error lines; nvrm=: every NVRM line, repeats included),
#   device and load average before/after. journal=complete|unknown records coverage; failed/partial reads print
#   kernel=unknown nvrm=unknown, with the journal receipt in kernel-status.json and retained lines in kernel.txt.
set -uo pipefail
H=/home/mooki/Games/.hookshot-harness
source "$(dirname -- "${BASH_SOURCE[0]}")/hs-names.sh"
NV_ICD=/usr/share/vulkan/icd.d/nvidia_icd.json
INTEL_ICD=/usr/share/vulkan/icd.d/intel_icd.json

WCLASS=ls-harness
window_refused() {
    cat <<EOF
hs-run.sh: --window is switched off: $H/window-ok does not exist. Nothing was started.
A windowed run opens a window that covers the user's desktop (never focused; reports/WindowRuns.md). The main session
creates that file while he is away from the PC and removes it when he is back; agents never create it.
EOF
}

# ---------------------------------------------------------------- inside the gate
if [ "${1:-}" = --inner ]; then
    R=${2:?}
    copy=$(cat "$R/copy.txt")
    hs_names copy "$copy" || exit 2
    seconds=$(cat "$R/seconds.txt")
    date '+%F %T' > "$R/start.txt"
    cut -d' ' -f1-3 /proc/loadavg > "$R/load-before.txt"
    unset HYPRLAND_INSTANCE_SIGNATURE DISPLAY XDG_ACTIVATION_TOKEN DESKTOP_STARTUP_ID
    if [ -f "$R/window" ]; then
        # (the turn may have come hours after the run was asked: the switch again, then one windowed run at a time)
        [ -e "$H/window-ok" ] || { window_refused | tee "$R/window-refused.txt" >&2; exit 5; }
        exec {wlock}> "$H/window.lock"
        flock "$wlock"
        winfo=$(python3 -I "$H/hs-window.py" rules "$R") || exit 3
        read -r _ wsock _ wlogical <<< "$winfo"
        export HOS_WINDOW_SIZE=$wlogical
    else
        unset WAYLAND_DISPLAY
        export HOS_HEADLESS=1
    fi
    export HOS_AUDIO=0 SDL_AUDIODRIVER=dummy SDL_AUDIO_DRIVER=dummy "${HS_ENV_PREFIX}_NO_UPDATE_CHECK=1" HOS_DISCORD=0 \
        HOS_INPUT_BACKGROUND=0 VK_DRIVER_FILES=$NV_ICD RX_VK_DEVICE=0 \
        RX_VK_SHOTS="$R/shots" RX_VK_FRAME_LOG="$R/frames.csv" HOS_PRESENT_TRACE="$R/present.csv" HOS_ENV_FILE="$R/env-file.txt" \
        __GL_SHADER_DISK_CACHE_PATH="$copy/.nv-cache" MESA_SHADER_CACHE_DIR="$copy/.mesa-cache" \
        XDG_CACHE_HOME="$copy/.xdg-cache" XDG_DATA_HOME="$R/xdg-data"
    mkdir -p "$R/shots" "$copy/.nv-cache" "$copy/.mesa-cache" "$copy/.xdg-cache" "$R/xdg-data"
    [ -f "$R/intel" ] && export VK_DRIVER_FILES=$INTEL_ICD
    if [ -s "$R/input.txt" ]; then export HOS_INPUT_SCRIPT="$(cat "$R/input.txt")" HOS_INPUT_EXCLUSIVE=1; fi
    while IFS= read -r kv; do [ -n "$kv" ] && export "$kv"; done < "$R/extra-env.txt"
    [ -f "$R/perf" ] && export RX_VK_PERF_LOG="$R" RX_VK_PERF_FIELDS=all
    if [ -f "$R/window" ]; then   # (after the caller's switches: nothing of theirs moves the window off its class or socket)
        unset HOS_HEADLESS DISPLAY
        export WAYLAND_DISPLAY=$wsock XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)} \
            SDL_APP_ID=$WCLASS SDL_VIDEO_WAYLAND_WMCLASS=$WCLASS SDL_VIDEODRIVER=wayland SDL_VIDEO_DRIVER=wayland
    fi
    env | LC_ALL=C sort | grep -E '^(HOS_|RX_|HOOKSHOT_|LONGSHOT_|VK_|SDL_|__GL_|MESA_|XDG_|HSGATE_IN_GATE)' > "$R/env-used.txt"
    t0=$(date +%s.%N)
    cd "$copy" || exit 1
    gargs=(--user "$R/user")
    [ -f "$R/prepare" ] && gargs+=(--prepare-shaders)
    pre=()
    [ -s "$R/memmax" ] && pre=(systemd-run --user --scope -q -p MemoryMax="$(cat "$R/memmax")" -p MemorySwapMax=0)
    if [ -f "$R/window" ]; then
        "${pre[@]}" timeout --signal=TERM --kill-after=20 "$seconds" "./$HS_EXE" "${gargs[@]}" > "$R/$HS_LOG" 2>&1 < /dev/null &
        tpid=$!
        python3 -I "$H/hs-window.py" watch "$R" "$tpid" > "$R/window-watch.log" 2>&1 < /dev/null &
        wpid=$!
        wait "$tpid"
        rc=$?
        wait "$wpid"
    else
        "${pre[@]}" timeout --signal=TERM --kill-after=20 "$seconds" "./$HS_EXE" "${gargs[@]}" > "$R/$HS_LOG" 2>&1 < /dev/null
        rc=$?
    fi
    echo "$rc" > "$R/exit.txt"
    python3 -I -c "import sys,time; print('%.1f' % (time.time() - float(sys.argv[1])))" "$t0" > "$R/wall.txt"
    cut -d' ' -f1-3 /proc/loadavg > "$R/load-after.txt"
    date '+%F %T' > "$R/end.txt"
    exit 0
fi

# ---------------------------------------------------------------- the caller's side
unset HSGATE_SLOT_DIR HSGATE_INTEL_SLOT_DIR HSGATE_SLOT_TEST HSGATE_SLOT_NODESKTOP HSGATE_IN_GATE
usage() { sed -n '5,6p' "$0" >&2; exit 2; }
[ $# -ge 3 ] || usage
copy=$(cd "$1" 2>/dev/null && pwd) || { echo "hs-run.sh: no folder $1" >&2; exit 2; }
tag=$2 seconds=$3
shift 3
case "$copy" in /home/mooki/Games/.hookshot-h-*) ;; *) echo "hs-run.sh: $copy is not a harness copy (/home/mooki/Games/.hookshot-h-*)" >&2; exit 2 ;; esac
hs_names copy "$copy" || exit 2
[ -x "$copy/$HS_EXE" ] && [ -f "$copy/image.bin" ] && [ -d "$copy/romfs" ] || { echo "hs-run.sh: $copy has no built game" >&2; exit 2; }
[[ $tag =~ ^[A-Za-z0-9._-]+$ ]] || { echo "hs-run.sh: tag: letters, digits, . _ - only" >&2; exit 2; }
[[ $seconds =~ ^[0-9]+$ ]] || usage
window=0 excl=() from=0 input="" usersrc="" keep=0 prepare=0 perf=0 intel=0 memmax="" extra=()
while [ $# -gt 0 ]; do
    case "$1" in
        --window) window=1; shift ;;
        --exclusive) excl+=(--exclusive); shift ;;
        --whole) excl+=(--whole); shift ;;
        --priority) excl+=(--priority); shift ;;
        --from) from=${2:?}; shift 2 ;;
        --input) input=${2?}; shift 2 ;;
        --user) usersrc=$(cd "${2:?}" 2>/dev/null && pwd) || { echo "hs-run.sh: no folder $2" >&2; exit 2; }; shift 2 ;;
        --keep-user) keep=1; shift ;;
        --prepare) prepare=1; shift ;;
        --perf) perf=1; shift ;;
        --intel) intel=1; shift ;;
        --mem-max) memmax=${2:?}; [[ $memmax =~ ^[0-9]+[KMGT]?$ ]] || usage; shift 2 ;;
        *=*) extra+=("$1"); shift ;;
        *) usage ;;
    esac
done
if [ "$window" = 1 ]; then
    [ "$intel" = 0 ] && [ "$prepare" = 0 ] || { echo "hs-run.sh: --window goes with neither --intel nor --prepare" >&2; exit 2; }
    for kv in "${extra[@]}"; do
        case "$kv" in HOS_HEADLESS=*) echo "hs-run.sh: --window and HOS_HEADLESS contradict each other" >&2; exit 2 ;; esac
    done
    [ -e "$H/window-ok" ] || { window_refused >&2; exit 2; }
fi
[ "$intel" = 1 ] && [ ${#excl[@]} -gt 0 ] && { echo "hs-run.sh: --exclusive, --whole and --priority are the card's; the Intel gate has none" >&2; exit 2; }
# A picture larger than the game's own 2048x1152 takes more of the card (3840x2160: 9.8 to 11.6 GB with the desktop's
# 1 GB, against 8.5 to 8.9 GB), and the gate lets a second game in beside one on the promise that both have the default
# picture. So such a run asks the gate for the whole card. The picture the game will use (host/nn_applet.c, read at its
# start): HOS_OUTPUT_RES (a NAME=VALUE of this call, else this call's environment), and only without it output_res= in
# the copy's detected options file. Larger means "<w>x<h>" with more pixels than 2048x1152, or anything this cannot
# read as a size within it (the game would fall back to its own size; this does not bet on that). The numbers saved
# by old versions: 0 the game's own and 3 1920x1080 are within, 1 2 4 5 are larger. Read when the run is asked: a
# run that changes the copy's options file while this one waits is not seen.
size_large() {   # $1: a value of HOS_OUTPUT_RES or output_res
    case "$1" in ''|0|3) return 1 ;; esac
    [[ $1 =~ ^([0-9]{1,5})x([0-9]{1,5})$ ]] || return 0
    [ $((10#${BASH_REMATCH[1]} * 10#${BASH_REMATCH[2]})) -gt $((2048 * 1152)) ]
}
picture_large() {   # $1: the copy; then this call's NAME=VALUE arguments
    local copy=$1 kv v="" given=0
    shift
    if [ -n "${HOS_OUTPUT_RES+x}" ]; then v=$HOS_OUTPUT_RES; given=1; fi
    for kv in "$@"; do case "$kv" in HOS_OUTPUT_RES=*) v=${kv#*=}; given=1 ;; esac; done
    if [ "$given" = 1 ]; then size_large "$v"; return; fi
    while IFS= read -r v; do
        size_large "$v" && return 0
    done < <(sed -n 's/^output_res=//p' "$copy/$HS_OPTIONS" 2>/dev/null | tr -d '\r')
    return 1
}
# A windowed run also takes the whole card (main session, at the merge of the window and the two-slot versions): what
# it is for (presentation, frame pacing, frame generation's output) is not judged with another game beside it.
if [ "$intel" = 0 ] && [[ " ${excl[*]} " != *" --whole "* ]] && [[ " ${excl[*]} " != *" --exclusive "* ]] &&
   { [ "$window" = 1 ] || picture_large "$copy" "${extra[@]}"; }; then
    excl+=(--whole)
fi
[ -z "$usersrc" ] && [ -d "$copy/user-template" ] && usersrc=$copy/user-template
R=$copy/runs/$tag
rm -rf "$R" && mkdir -p "$R"
if [ -n "$usersrc" ]; then cp -a --reflink=auto "$usersrc" "$R/user"; else mkdir -p "$R/user"; fi
echo "$copy" > "$R/copy.txt"
echo "$seconds" > "$R/seconds.txt"
printf '%s' "$input" > "$R/input.txt"
[ "$prepare" = 1 ] && : > "$R/prepare"
[ "$perf" = 1 ] && : > "$R/perf"
[ "$intel" = 1 ] && : > "$R/intel"
[ "$window" = 1 ] && : > "$R/window"
printf '%s' "$memmax" > "$R/memmax"
printf '%s\n' "${extra[@]}" > "$R/extra-env.txt"
echo "# empty: hs-run.sh passes its switches in the environment" > "$R/env-file.txt"
{ echo "copy=$copy tag=$tag seconds=$seconds from=$from user=${usersrc:-<new>} input=$input ${excl[*]} window=$window"
  echo "extra: ${extra[*]}"; } > "$R/args.txt"
asked=$(date '+%F %T')
if [ "$intel" = 1 ]; then
    /home/mooki/Games/hs-intel-slot "$0" --inner "$R"
else
    /home/mooki/Games/hs-gpu-slot "${excl[@]}" "$0" --inner "$R"
fi
gate=$?
if [ ! -f "$R/exit.txt" ]; then
    why=""
    [ -f "$R/window-refused.txt" ] && why=": $(head -n 1 "$R/window-refused.txt")"
    echo "hs-run $tag: the gate did not run the game (gate exit $gate, asked $asked)$why" | tee "$R/summary.txt"
    exit "$gate"
fi
[ "$keep" = 1 ] || find "$R/user" -mindepth 1 -maxdepth 1 ! -name save -exec rm -rf {} +
home_writes=""
for p in "$HOME/.local/share/$HS_PROJECT" "$HOME/.cache/$HS_PROJECT-builder"; do [ -e "$p" ] && home_writes="$home_writes $p"; done
line=$(python3 -I -B - "$R" "$from" "$tag" "$home_writes" "$intel" "$H/regress/run_case.py" <<'PY'
import csv, glob, importlib.util, json, os, re, subprocess, sys
R, frm, tag, home = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4].strip()
def rd(name):
    with open(os.path.join(R, name)) as f:
        return f.read().strip()
spec = importlib.util.spec_from_file_location("hs_run_kernel", sys.argv[6])
kernel_reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel_reader)
from hs_names import log_path
kernel_text, kernel_info = kernel_reader.kernel_lines(rd("start.txt"), rd("end.txt"), sys.argv[5] == "1")
with open(os.path.join(R, "kernel.txt"), "w") as f:
    f.write(kernel_text)
ft, span, src, rows = [], 0.0, "-", []
if os.path.exists(os.path.join(R, "frames.csv")):
    with open(os.path.join(R, "frames.csv")) as f:
        rows = [(float(r[1]), float(r[2])) for r in list(csv.reader(f))[1:] if len(r) == 3]
    if rows:
        src = "swapchain" if os.path.exists(os.path.join(R, "window")) else "headless-renderer"
if not rows and os.path.exists(os.path.join(R, "present.csv")):
    raw = kernel_reader.present_rows(os.path.join(R, "present.csv"))
    t0 = int(raw[0][1]) if raw else 0
    rows = [((int(r[1]) - t0) / 1e9, int(r[2]) / 1e6) for r in raw]
    if rows:
        src = "game"
span = rows[-1][0] if rows else 0.0
ft = [ms for t, ms in rows if t >= frm][1:]   # (the first frame of the window has no frame before it inside the window)
if ft:
    mean = 1000.0 * len(ft) / sum(ft)
    slow = sorted(ft, reverse=True)[:max(1, len(ft) // 100)]
    low1 = 1000.0 * len(slow) / sum(slow)
    o16, o20, o25 = (sum(1 for x in ft if x > lim) for lim in (16.7, 20.0, 25.0))
    stats = "frames=%d(%s) mean=%.1f low1=%.1f over16.7=%d over20=%d over25=%d" % (len(ft), src, mean, low1, o16, o20, o25)
else:
    stats = "frames=0 mean=- low1=- over16.7=- over20=- over25=-"
shots = sorted(glob.glob(os.path.join(R, "shots", "*.png")))
size = "-"
if shots:
    try:
        size = subprocess.run(["magick", "identify", "-format", "%wx%h", shots[-1]], capture_output=True, text=True).stdout.strip() or "?"
    except OSError:
        size = "?"
mb = sum(os.path.getsize(p) for p in shots) / 1e6
with log_path(R, required=True).open(errors="replace") as f:
    log = f.read()
fatal = len(re.findall(r"FATAL", log))
lost = len(re.findall(r"(?i)device lost|VK_ERROR_DEVICE_LOST", log))
crash = len(re.findall(r"CRASH:", log))
failed = bool(fatal or crash or lost or rd("exit.txt") not in ("0", "124", "143"))
if failed and kernel_info["kernel_errors"] is not None and 0 < kernel_info["no_memory_events"] <= 3:
    kernel_info["kernel_errors"] += kernel_info["no_memory_lines"]
with open(os.path.join(R, "kernel-status.json"), "w") as f:
    json.dump(kernel_info, f, indent=2)
    f.write("\n")
kernel = kernel_info["kernel_errors"] if kernel_info["kernel_errors"] is not None else "unknown"
nvrm = kernel_info["nvrm_lines"] if kernel_info["nvrm_lines"] is not None else "unknown"
nocore = len(re.findall(r"ran without a core", log))
devs = re.findall(r"vk: device (\d+): ([^,]+),", log)
dev = "/".join("%s:%s" % d for d in devs) or "-"
want = "Intel" if sys.argv[5] == "1" else "4090"
gpu_ok = "" if devs and all(want in d[1] for d in devs) else " DEVICE?"
win = ""
if os.path.exists(os.path.join(R, "window")):   # (a windowed run: the swapchain, the game's own rate, hs-window.py's line)
    made = re.findall(r"vk: swapchain (\d+x\d+), \d+ images, (\w+) \(V-Sync (\w+)\)", log)
    win = " swapchain=%s" % ("%s,%s,vsync-%s,x%d" % (*made[-1], len(made)) if made else "NONE")
    game = "-"
    if src == "swapchain" and os.path.exists(os.path.join(R, "present.csv")):
        raw = kernel_reader.present_rows(os.path.join(R, "present.csv"))
        last = int(raw[-1][1]) if raw else 0
        g = [int(r[2]) / 1e6 for r in raw if (last - int(r[1])) / 1e9 <= span - frm][1:]
        if g:
            game = "%.1f" % (1000.0 * len(g) / sum(g))
    win += " game=%s " % game
    win += rd("window.txt") if os.path.exists(os.path.join(R, "window.txt")) else "window=UNWATCHED"
print("hs-run %s exit=%s wall=%ss span=%.1fs from=%gs %s shots=%d %s %.1fMB fatal=%d crash=%d devlost=%d nocore=%d kernel=%s nvrm=%s dev=%s%s%s load %s -> %s%s journal=%s"
      % (tag, rd("exit.txt"), rd("wall.txt"), span, frm, stats, len(shots), size, mb, fatal, crash, lost, nocore, kernel, nvrm,
         dev.replace(" ", "_"), gpu_ok, win, rd("load-before.txt"), rd("load-after.txt"),
         (" HOME-WRITES:" + home) if home else "", kernel_info["kernel_status"]))
PY
) || exit 1
echo "$line" | tee "$R/summary.txt"
echo "$(cat "$R/start.txt") $line" >> "$H/runs.log"
exit 0
