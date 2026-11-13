#!/bin/bash
# hs-pr-check.sh <branch> [<title file>] [--imported <commit>=<original>]... [--imported-files <commit>=<source>]... :
# the mechanical checks before an LS branch is pushed and opened as a pull request (skill ls-pr-evidence,
# "Before any push"). Prints one line per check and ends with PASS or FAIL. It reads only; it pushes nothing. The diff
# itself is still read by the main session.
# --imported names one commit of the branch that carries another contributor's commit unchanged (a cherry-pick). It is
# proven against the original (same patch, author and message; committed by the user) and then keeps its own author and
# message: the identity and trailer checks leave it out. Never rewrite someone else's authorship to make this pass.
# --imported-files names one commit of the branch that carries some of another contributor's FILES unchanged, where
# their original commits cannot be picked whole (they also touch files we do not take). It is proven against a source
# commit: every file the commit touches is byte-identical to the same path in the source's tree, the author is the
# source commit's author, the message has a line "Imported from <repo>#<n> at <source hash>", and the user committed it.
set -u
repo=/home/mooki/Code/Hookshot
usage='usage: hs-pr-check.sh <branch> [<title file>] [--imported <commit>=<original>]... [--imported-files <commit>=<source>]...'
me='7663497+mooki000@users.noreply.github.com'
branch=; title_file=; imported=(); imported_files=()
while [ $# -gt 0 ]; do
    case "$1" in
        --imported) [ $# -ge 2 ] && [ "${2#*=}" != "$2" ] || { echo "$usage" >&2; exit 2; }; imported+=("$2"); shift 2 ;;
        --imported-files) [ $# -ge 2 ] && [ "${2#*=}" != "$2" ] || { echo "$usage" >&2; exit 2; }; imported_files+=("$2"); shift 2 ;;
        -*) echo "$usage" >&2; exit 2 ;;
        *) if [ -z "$branch" ]; then branch=$1; elif [ -z "$title_file" ]; then title_file=$1; else echo "$usage" >&2; exit 2; fi; shift ;;
    esac
done
[ -n "$branch" ] || { echo "$usage" >&2; exit 2; }
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

# 2. identity (imported commits are proven against their originals first, then left out of checks 2 and 3)
exempt=' '
for pair in ${imported[@]+"${imported[@]}"}; do
    ours=$(git -C "$repo" rev-parse -q --verify "${pair%%=*}^{commit}") || { say FAIL "imported: no commit ${pair%%=*}"; continue; }
    orig=$(git -C "$repo" rev-parse -q --verify "${pair#*=}^{commit}") || { say FAIL "imported: no original commit ${pair#*=}"; continue; }
    if [ "$ours" = "$orig" ] || ! git -C "$repo" rev-list "$base..$branch" | grep -q -x "$ours" || git -C "$repo" rev-list "$base..$branch" | grep -q -x "$orig"; then
        say FAIL "imported: ${ours:0:9} must be one of the branch's own commits and ${orig:0:9} a different commit outside it"; continue
    fi
    pa=$(git -C "$repo" show "$ours" | git patch-id --stable | cut -d' ' -f1)
    pb=$(git -C "$repo" show "$orig" | git patch-id --stable | cut -d' ' -f1)
    ia=$(git -C "$repo" log -1 --format='%an <%ae>%n%B' "$ours"); ib=$(git -C "$repo" log -1 --format='%an <%ae>%n%B' "$orig")
    if [ -n "$pa" ] && [ "$pa" = "$pb" ] && [ "$ia" = "$ib" ] && [ "$(git -C "$repo" log -1 --format=%ce "$ours")" = "$me" ]; then
        exempt="$exempt$ours "
        say OK "imported ${ours:0:9} = ${orig:0:9}: same patch, author ($(git -C "$repo" log -1 --format=%an "$ours")) and message; committed by the user. Name its author in the pull request's description."
    else
        say FAIL "imported ${ours:0:9} differs from ${orig:0:9} in patch, author or message, or was not committed by the user"
    fi
done
for pair in ${imported_files[@]+"${imported_files[@]}"}; do
    ours=$(git -C "$repo" rev-parse -q --verify "${pair%%=*}^{commit}") || { say FAIL "imported-files: no commit ${pair%%=*}"; continue; }
    src=$(git -C "$repo" rev-parse -q --verify "${pair#*=}^{commit}") || { say FAIL "imported-files: no source commit ${pair#*=}"; continue; }
    if [ "$ours" = "$src" ] || ! git -C "$repo" rev-list "$base..$branch" | grep -q -x "$ours" || git -C "$repo" rev-list "$base..$branch" | grep -q -x "$src"; then
        say FAIL "imported-files: ${ours:0:9} must be one of the branch's own commits and ${src:0:9} a different commit outside it"; continue
    fi
    paths=$(git -C "$repo" diff-tree --no-commit-id -r --name-only "$ours")
    differ=0; count=0
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        count=$((count + 1))
        a=$(git -C "$repo" rev-parse -q --verify "$ours:$p" 2>/dev/null); b=$(git -C "$repo" rev-parse -q --verify "$src:$p" 2>/dev/null)
        [ -n "$a" ] && [ "$a" = "$b" ] || { differ=$((differ + 1)); echo "      not the source's file: $p"; }
    done <<< "$paths"
    same_author=0; [ "$(git -C "$repo" log -1 --format='%an <%ae>' "$ours")" = "$(git -C "$repo" log -1 --format='%an <%ae>' "$src")" ] && same_author=1
    line=0; git -C "$repo" log -1 --format=%B "$ours" | grep -q -E "^Imported from [A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+ at ${src:0:8}[0-9a-f]*\b" && line=1
    if [ "$count" -gt 0 ] && [ "$differ" -eq 0 ] && [ "$same_author" -eq 1 ] && [ "$line" -eq 1 ] && [ "$(git -C "$repo" log -1 --format=%ce "$ours")" = "$me" ]; then
        exempt="$exempt$ours "
        say OK "imported files ${ours:0:9} from ${src:0:9}: $count files byte-identical to the source's, author $(git -C "$repo" log -1 --format=%an "$ours"), provenance line present; committed by the user. Name the author in the pull request's description."
    else
        say FAIL "imported-files ${ours:0:9}: $differ of $count files differ from ${src:0:9}, or the author (same=$same_author), the 'Imported from <repo>#<n> at <hash>' line (present=$line) or the committer is wrong"
    fi
done
bad=0
for c in $(git -C "$repo" rev-list "$base..$branch"); do
    case "$exempt" in *" $c "*) continue ;; esac
    git -C "$repo" log -1 --format='%ae %ce' "$c" | grep -q -E "^7663497\+mooki000@users\.noreply\.github\.com (7663497\+mooki000@users\.noreply\.github\.com|noreply@github\.com)$" || bad=$((bad + 1))
done
[ "$bad" -eq 0 ] && say OK "every commit of ours is by the user's identity" || { say FAIL "$bad commits with another author or committer (an unchanged commit of someone else's needs --imported):"; git -C "$repo" log --format='  %h %an <%ae> / %cn <%ce>' "$base..$branch" | sort -u | head -n 5; }

# 3. trailers (the maintainers record AI assistance with both)
miss=0
for c in $(git -C "$repo" rev-list "$base..$branch"); do
    case "$exempt" in *" $c "*) continue ;; esac
    msg=$(git -C "$repo" log -1 --format=%B "$c")
    echo "$msg" | grep -q '^Assisted-by: ' && echo "$msg" | grep -q -i '^Co-Authored-By: ' || { miss=$((miss + 1)); [ "$miss" -le 3 ] && echo "      missing trailers: $(git -C "$repo" log -1 --format='%h %s' "$c" | cut -c1-90)"; }
done
[ "$miss" -eq 0 ] && say OK "every commit has the Assisted-by and Co-Authored-By trailers" || say WARN "$miss commits lack a trailer (the title and the squash decide; add them to the pull request body's end if not amended)"

# 4. nothing made from the game, no build output
files=$(git -C "$repo" diff --name-only "$base" "$branch")
badf=$(echo "$files" | grep -E '(^|/)work/|\.(nup|nsp|xci|nca|nso|sav|bin|bwav|bars|byml|zs|sarc|bfres|bntx|webm|spv|so|dll|exe|AppImage|o|a)$|(hookshot|longshot)_options\.ini|(hookshot|longshot)_log' | grep -v -E '_spv\.h$' || true)
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
