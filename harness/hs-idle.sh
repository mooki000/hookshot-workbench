#!/bin/bash
# hs-idle.sh <command> [args...] : runs a CPU-only job (the game-free checks, tests/run_all.py; a sanitizer test) in the
# idle scheduling class, in one of three test slots. Like a build (hs-build-slot) it does not start once an
# exclusive GPU timing run's turn has come (the quiet-wanted mark of hs-gpu-slot --exclusive), and it holds the
# quiet lock shared while it runs, so a frame-rate measurement never has the checks beside it: an idle-class job still
# takes the second thread of a core the game runs on. It takes no build slot. Written 2026-11-10, when six runs of the
# checks beside four builds put the load at 170 during timing holds.
#   hs-idle.sh /home/mooki/Code/hookshot-wt/deps/python/bin/python3 -I -B tests/run_all.py
set -euo pipefail
dir=/tmp/hookshot-test-slots
gpu=/tmp/hs-gpu-slots
mkdir -p "$dir" "$gpu"
while :; do
    wanted=0
    for m in "$gpu"/quiet-wanted.*; do
        [ -e "$m" ] || continue
        if grep -qa hs-gpu-slot "/proc/${m##*.}/cmdline" 2>/dev/null; then
            wanted=1
        fi
    done
    if [ "$wanted" = 0 ]; then
        for i in 0 1 2; do
            exec 9>"$dir/$i"
            if flock -n 9; then
                exec 8>"$gpu/quiet"
                flock -s 8
                exec chrt --idle 0 nice -n 15 /home/mooki/Games/.hookshot-harness/hs-nocore.sh "$@"
            fi
        done
    fi
    sleep 2
done
