#!/bin/bash
# hs-pr-check.sh <branch> [<title file>] : the mechanical checks before a Hookshot branch is pushed and opened as a
# pull request (skill hookshot-pr-evidence, "Before any push"). Prints one line per check and ends with PASS or FAIL.
# It reads only; it pushes nothing. The diff itself is still read by the main session.
set -u
repo=/home/mooki/Code/Hookshot
branch=${1:?usage: hs-pr-check.sh <branch> [<title file>]}
title_file=${2:-}
fail=0
say() { printf '%-5s %s\n' "$1" "$2"; [ "$1" = FAIL ] && fail=1; return 0; }

git -C "$repo" fetch -q origin 2>/dev/null || say WARN "could not fetch origin; comparing with the last fetched main"
git -C "$repo" rev-parse -q --verify "$branch" > /dev/null || { say FAIL "no branch $branch in $repo"; echo FAIL; exit 1; }
main=$(git -C "$repo" rev-parse --short origin/main)
base=$(git -C "$repo" merge-base origin/main "$branch")
n=$(git -C "$repo" rev-list --count "$base..$branch")
behind=$(git -C "$repo" rev-list --count "$base..origin/main")
say INFO "branch $branch: $n commits on $(git -C "$repo" rev-parse --short "$base"); origin/main is $main"

# 1. on current main (a pull request that does not merge cleanly waits)
if [ "$behind" -eq 0 ]; then say OK "based on current main"
else
    if git -C "$repo" merge-tree --write-tree origin/main "$branch" > /dev/null 2>&1; then
        say WARN "main has moved by $behind commits; the branch still merges cleanly (rebase before review is asked)"
    else
        say FAIL "main has moved by $behind commits and the branch no longer merges: rebase"
    fi
fi
[ "$n" -gt 0 ] || say FAIL "the branch has no commits of its own"

# 2. identity
bad=$(git -C "$repo" log --format='%ae %ce' "$base..$branch" | grep -c -v -E '^7663497\+mooki000@users\.noreply\.github\.com (7663497\+mooki000@users\.noreply\.github\.com|noreply@github\.com)$')
[ "$bad" -eq 0 ] && say OK "every commit is by the user's identity" || { say FAIL "$bad commits with another author or committer:"; git -C "$repo" log --format='  %h %an <%ae> / %cn <%ce>' "$base..$branch" | sort -u | head -n 5; }

# 3. trailers (the maintainers record AI assistance with both)
miss=0
for c in $(git -C "$repo" rev-list "$base..$branch"); do
    msg=$(git -C "$repo" log -1 --format=%B "$c")
    echo "$msg" | grep -q '^Assisted-by: ' && echo "$msg" | grep -q -i '^Co-Authored-By: ' || { miss=$((miss + 1)); [ "$miss" -le 3 ] && echo "      missing trailers: $(git -C "$repo" log -1 --format='%h %s' "$c" | cut -c1-90)"; }
done
[ "$miss" -eq 0 ] && say OK "every commit has the Assisted-by and Co-Authored-By trailers" || say WARN "$miss commits lack a trailer (the title and the squash decide; add them to the pull request body's end if not amended)"

# 4. nothing made from the game, no build output
files=$(git -C "$repo" diff --name-only "$base" "$branch")
badf=$(echo "$files" | grep -E '(^|/)work/|\.(nup|nsp|xci|nca|nso|sav|bin|bwav|bars|byml|zs|sarc|bfres|bntx|webm|spv|so|dll|exe|AppImage|o|a)$|hookshot_options\.ini|hookshot_log' | grep -v -E '_spv\.h$' || true)
[ -z "$badf" ] && say OK "no game file, save, log or build output among $(echo "$files" | grep -c .) changed files" || { say FAIL "files that must not be in a pull request:"; echo "$badf" | head -n 8 | sed 's/^/      /'; }
big=$(git -C "$repo" diff --numstat "$base" "$branch" | awk '$1 == "-" {print $3}')
[ -z "$big" ] || { say WARN "binary files in the diff (look at each):"; echo "$big" | head -n 8 | sed 's/^/      /'; }

# 5. nothing of another project by name: the words are one extended regex in pr-check-forbidden.txt beside this script
#    (a local file, not published; without it this check is left out)
forbid=$(head -n 1 "$(dirname "$0")/pr-check-forbidden.txt" 2>/dev/null || true)
if [ -n "$forbid" ]; then
    hits=$(git -C "$repo" diff "$base" "$branch" | grep -E '^\+' | grep -v '^+++' | grep -c -i -E "$forbid" || true)
    [ "$hits" -eq 0 ] && say OK "no mention of the other port in added lines" || say FAIL "$hits added lines name the other port (read them: nothing of its code may come over)"
else
    say WARN "pr-check-forbidden.txt is missing: the check for the other port's name was left out"
fi

# 6. a test comes with it
tests=$(echo "$files" | grep -c -E '^tests/' || true)
[ "$tests" -gt 0 ] && say OK "$tests files under tests/ changed or added" || say WARN "no file under tests/ changed: the project asks for a game-free test that fails without the change"

# 7. the title is a Conventional Commit
if [ -n "$title_file" ]; then
    t=$(head -n 1 "$title_file")
    if echo "$t" | grep -q -E '^(feat|fix|perf|refactor|docs|test|ci|build|chore|revert)(\([a-z0-9_./-]+\))?!?: [^ ].+$'; then
        say OK "title: $t"
        [ "${#t}" -le 100 ] || say WARN "the title has ${#t} characters; keep it short"
    else
        say FAIL "the title is not a Conventional Commit: $t"
    fi
fi

[ "$fail" -eq 0 ] && echo PASS || echo FAIL
exit "$fail"
