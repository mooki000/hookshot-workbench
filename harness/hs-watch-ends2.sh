#!/bin/bash
# Main session: wakes it when one of the runs it is waiting for has ended. The list is a file read at every round
# (so entries can be added without restarting): one entry a line, "<name>|<file>|<marker as an extended regex>".
# hs-watch-ends2.sh <list file> <state file>   (the state file lists what was already reported)
list=${1:?} state=${2:?}
touch "$state"
start=$(date +%s)
while :; do
    hit=0
    while IFS='|' read -r name file marker; do
        [ -n "$name" ] && [ "${name:0:1}" != "#" ] || continue
        grep -qx "$name" "$state" && continue
        [ -f "$file" ] && grep -q -E "$marker" "$file" 2>/dev/null || continue
        echo "$name" >> "$state"
        echo "ENDED: $name ($file) at $(date '+%H:%M:%S')"
        tail -n 4 "$file" | cut -c1-260
        hit=1
    done < "$list"
    [ "$hit" = 1 ] && exit 0
    [ $(( $(date +%s) - start )) -gt 14400 ] && { echo "watch-ends: nothing in 4 h"; exit 0; }
    sleep 45
done
