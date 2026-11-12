#!/usr/bin/env bash
# Rewrites the home folder these tools were written for into yours, in every file of this checkout.
#
#   ./relocate.sh /home/<you>
#
# Afterwards put the folders where the README's table says (or change those paths too) and read the top of
# gates/hs-gpu-slot: its memory numbers are for a 24 GB card and 96 GB of RAM.
set -euo pipefail
new=${1:?usage: relocate.sh /home/<you>}
case $new in /*) ;; *) echo "an absolute path, please" >&2; exit 2 ;; esac
here=$(cd "$(dirname "$0")" && pwd)
old=/home/mooki
grep -rlI --exclude-dir=.git --exclude=relocate.sh --exclude=sync-from-live.sh -- "$old" "$here" | while read -r f; do
    sed -i "s|$old|$new|g" "$f"
    echo "rewrote ${f#$here/}"
done
