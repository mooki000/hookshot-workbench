#!/usr/bin/env bash
# What must never be in this repository, checked over every file of the checkout before a commit:
#   1. game files, saves, build output, and the data files my local tools make from the game (the world model and the
#      segment files are JSON and TOML: no such file belongs here at all)
#   2. a file over 200 KB (nothing here is that large; a large file is data)
#   3. anything that looks like a key or a token
#   4. private addresses
#   6. words from a private list (when the list is present)
# and, as lines to READ rather than a verdict, 5. what smells like facts read out of the game (coordinates, flag and
# object names). A map name or a hash that Hookshot's own source or a public pull request already has is fine; a list of
# places is not. The one exception is docs/oot/, the map of the game's lifted functions: addresses, names of our own and
# what a function does in our own words (docs/oot/README.md, "What may go here"); it is not read for check 5, and it
# still must hold no game file, no lifted code and nothing over 200 KB. Prints one line per check and ends with PASS or FAIL.
# The MCP project's exact package manifest and lock are source metadata, not game data.
set -u
here=$(cd "$(dirname "$0")" && pwd)
cd "$here"
fail=0
say() { printf '%-5s %s\n' "$1" "$2"; [ "$1" = FAIL ] && fail=1; return 0; }
files=$(git ls-files --cached --others --exclude-standard 2>/dev/null || find . -type f -not -path './.git/*' | sed 's|^\./||')

bad=$(echo "$files" | grep -v -x -E 'mcp/pyproject\.toml|mcp/uv\.lock' | grep -i -E '\.(sav|nup|nsp|xci|nca|nso|bin|zs|sarc|byml|bfres|bntx|bwav|bars|bfevfl|spv|so|dll|exe|appimage|o|a|pyc|pickle|gz|zip|7z|json|jsonl|toml|csv|png|jpe?g|webm|mp4|log)$|(^|/)(runs|saves|world|segments|routes)/|(hookshot|longshot)_(options\.ini|env\.txt)' || true)
[ -z "$bad" ] && say OK "no game file, save, data file, picture or log among $(echo "$files" | grep -c .) files" || { say FAIL "files that must not be here:"; echo "$bad" | sed 's/^/      /'; }

big=$(echo "$files" | grep -v -x -E 'mcp/pyproject\.toml|mcp/uv\.lock' | while read -r f; do [ -f "$f" ] && [ "$(stat -c %s "$f")" -gt 204800 ] && echo "$f"; done)
[ -z "$big" ] && say OK "no file over 200 KB" || { say FAIL "files over 200 KB:"; echo "$big" | sed 's/^/      /'; }

self='publish-check.sh'
keys=$(echo "$files" | grep -v -x "$self" | xargs -r grep -n -I -E 'sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|Bearer [A-Za-z0-9._-]{24,}|(api[_-]?key|secret|password|passwd|token) *[:=] *["'"'"'][^"'"'"' ]{12,}' 2>/dev/null || true)
[ -z "$keys" ] && say OK "nothing that looks like a key or a token" || { say FAIL "lines that look like a key or a token:"; echo "$keys" | cut -c1-160 | sed 's/^/      /'; }

priv=$(echo "$files" | grep -v -x "$self" | xargs -r grep -n -I -i -E '\b(10|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]{1,3}\.[0-9]{1,3}\b|[a-z0-9._%+-]+@(gmail|proton|outlook|icloud|yahoo)\.[a-z]+|/\.ssh/|id_(rsa|ed25519)' 2>/dev/null | grep -v -E '127\.0\.0\.' || true)
[ -z "$priv" ] && say OK "no private address" || { say FAIL "private addresses:"; echo "$priv" | cut -c1-160 | sed 's/^/      /'; }

smell=$(echo "$files" | grep -v -x "$self" | grep -v '^docs/oot/' | xargs -r grep -n -I -E '(-?[0-9]{1,4}\.[0-9]{1,2}[ ,]+){2}-?[0-9]{1,4}\.[0-9]{1,2}|\b(Step|Obj|Npc|Item|Weapon|Armor|IsGet|Demo|Dm)_[A-Za-z0-9_]{3,}|[A-Za-z]+_(Child|Adult)\b|(0x[0-9a-f]{8}[ ,]+){3}' 2>/dev/null || true)
if [ -z "$smell" ]; then say OK "nothing that smells like facts read out of the game"
else say READ "$(echo "$smell" | grep -c .) lines to read (facts about the game's content do not belong here):"; echo "$smell" | cut -c1-170 | sed 's/^/      /'; fi

# 6. words that must not be published at all (names that are only this machine's history): one pattern a line in the
#    private list, which is not in the repository. Without the list the check is skipped and says so.
if [ -f .private/publish-forbidden.txt ]; then
    words=$(echo "$files" | xargs -r grep -n -I -i -E -f .private/publish-forbidden.txt 2>/dev/null)
    names=$(echo "$files" | grep -i -E -f .private/publish-forbidden.txt)
    [ -z "$words$names" ] && say OK "none of the private list's $(grep -c . .private/publish-forbidden.txt) words" || { say FAIL "words from the private list:"; { echo "$names"; echo "$words"; } | grep . | cut -c1-160 | sed 's/^/      /'; }
else say SKIP "no private word list here"
fi

[ "$fail" -eq 0 ] && echo PASS || echo FAIL
exit "$fail"
