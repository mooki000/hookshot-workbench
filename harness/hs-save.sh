#!/bin/bash
# Copy a save of the Hookshot save library into a fresh throwaway user folder for one run. Written 2026-11-10 (SaveLib).
#
#   hs-save.sh <name> <target folder> [--voice <language>]
#   hs-save.sh --list
#
#   <name>     an entry of /home/mooki/Games/.hookshot-harness/saves/ (its README.txt says what it is), or the path of
#              any Hookshot user folder (one that holds save/<user id>/)
#   <target>   the folder to make: it must not exist, or be one this script made before (then it is replaced)
#   --voice    set the copy's Voice Language option (option.sav), e.g. pt-BR, ja, en-US, Portuguese_Brazil
#              (tools/hs_make_save.py --voice; the library entry itself is never changed)
#   --list     the entries, one line each (name, Voice Language, the slot's area, place and story)
#
# The copy holds only save/ (the game's files: account.sav, option.sav, slot_NN/); the run's game writes there, the
# library is never written. Then: hs-run.sh <copy> <tag> <seconds> --user <target> --input '<presses>' ...
# Saves are game data: they stay on this PC. Never attach, post, upload or commit them.
set -euo pipefail
H=/home/mooki/Games/.hookshot-harness
LIB=$H/saves
MARK=.hs-save
usage() { sed -n '4,15p' "$0" >&2; exit 2; }

if [ "${1:-}" = --list ]; then
    python3 -I - "$LIB/catalogue.json" <<'PY'
import json, sys
for n, m in json.load(open(sys.argv[1])).items():
    d = m.get('decoded', {})
    s = next((x for x in d.get('slots', []) if x.get('slot') == d.get('newest')), {})
    story = ' '.join(f'{k}={v}' for k, v in s.get('story', {}).items())
    texts = [p.split(': ', 1)[-1] for p in m.get('proven', [])]   # ("<date time>: <text>"; "LOADS, NOT YET..." = not at its point)
    proven = ('proven' if any(not t.startswith('LOADS, NOT YET') for t in texts) else
              'loads, not at its point yet' if texts else 'not yet run')
    print(f"{n:24s} {d.get('voice', '?'):18s} {s.get('age', '?')} in {s.get('area', '?')} at "
          f"{', '.join(f'{v:.1f}' for v in s.get('pos') or [])}; {s.get('time', '')}; {story} [{proven}]")
PY
    exit 0
fi
[ $# -ge 2 ] || usage
name=$1 target=$2
shift 2
voice=""
while [ $# -gt 0 ]; do
    case "$1" in
        --voice) voice=${2:?}; shift 2 ;;
        *) usage ;;
    esac
done

if [[ $name != */* ]] && [ -d "$LIB/$name/save" ]; then src=$LIB/$name
elif [ -d "$name/save" ]; then src=$(cd "$name" && pwd)
else echo "hs-save.sh: no library entry or user folder '$name' (hs-save.sh --list)" >&2; exit 2; fi
uid_dirs=("$src"/save/*/)
[ -d "${uid_dirs[0]}" ] || { echo "hs-save.sh: $src/save holds no user folder" >&2; exit 2; }

target=$(realpath -m "$target")
for p in "$HOME/.local/share/hookshot" "$HOME/Games/Hookshot" "$HOME/Games/Ocarina" "$HOME/Games/Ocarina-linux-next" \
         "$LIB" /home/mooki/Games/.hookshot-harness; do
    case "$target/" in "$p"/*) echo "hs-save.sh: refusing to write under $p" >&2; exit 2 ;; esac
done
if [ -e "$target" ]; then
    if [ -f "$target/$MARK" ]; then rm -rf "$target"
    elif [ -d "$target" ] && [ -z "$(ls -A "$target")" ]; then rmdir "$target"
    else echo "hs-save.sh: $target exists and was not made by hs-save.sh" >&2; exit 2; fi
fi
mkdir -p "$target"
cp -a --reflink=auto "$src/save" "$target/save"
printf 'copied from %s at %s\n' "$src" "$(date '+%F %T')" > "$target/$MARK"
if [ -n "$voice" ]; then
    acct=$(ls -d "$target"/save/*/ | head -n 1)
    set_to=$(python3 -I -B - "$H/tools" "$acct" "$voice" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
sys.argv = sys.argv[:1] + sys.argv[2:]
import hs_make_save as m
print(m.set_voice(sys.argv[1], m.Game(m.DEFAULT_GAME), sys.argv[2]))
PY
)
    echo "voice: option.sav set to $set_to" >> "$target/$MARK"
    echo "hs-save.sh: Voice Language set to $set_to in the copy"
fi
echo "$target"
if [ -f "$src/README.txt" ]; then
    sed -n '1p' "$src/README.txt"
    grep -m1 'Presses that continue' "$src/README.txt" || echo "  (not yet proven in Hookshot: see $src/README.txt)"
fi
