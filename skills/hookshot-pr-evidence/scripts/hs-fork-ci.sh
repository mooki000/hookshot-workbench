#!/usr/bin/env bash
# Runs the project's own checks (the Windows jobs included) on a commit BEFORE it reaches an upstream pull request, by
# way of a scratch draft pull request inside the user's fork. Use it for every commit that can behave differently on
# Windows: a new or changed C/C++ fixture, Python that touches files, paths, processes or locking, a workflow change.
#
#   hs-fork-ci.sh <commit> <label> [--keep] [--minutes N]
#
#   <commit>  what to check (a branch, a tag or a SHA of the current repository); it is pushed to the fork as
#             ci/<label>-<sha8> and nothing else is touched (never the fork's main: a push there can start its release
#             workflows; never the upstream branch)
#   <label>   a short name for the scratch branch and the draft's title: [a-z0-9-], e.g. pr2710 or pin-commit
#   --keep    leave the scratch draft and its branch (default: close the draft and delete the branch when the checks end)
#   --minutes how long to wait for the checks (default 30)
#
# Why a draft pull request and not a dispatch: the fork's main is far behind upstream, so its copy of the workflows has
# no dispatch trigger; a pull request inside the fork runs the workflows of the commit under test.
# Exit 0: every "Python source checks (windows-2022 ...)" job passed and no other job failed. Exit 1: a Windows job
# failed or is missing. Exit 2: only non-Windows jobs failed (read them: the fork's AppImage job can fail at a download,
# which says nothing about the commit). Exit 3: timed out. Prints one line per job either way.
# It uses the gh command the user is signed in with and reads no token itself.
set -euo pipefail
fork=${HS_FORK_REPO:-mooki000/Hookshot}
remote=${HS_FORK_REMOTE:-fork}
keep=0 minutes=30
[ $# -ge 2 ] || { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 64; }
commit=$1 label=$2; shift 2
while [ $# -gt 0 ]; do
    case $1 in
        --keep) keep=1; shift ;;
        --minutes) minutes=$2; shift 2 ;;
        *) echo "hs-fork-ci.sh: unknown option $1" >&2; exit 64 ;;
    esac
done
[[ $label =~ ^[a-z0-9][a-z0-9-]{0,39}$ ]] || { echo "hs-fork-ci.sh: label: lowercase letters, digits and dashes" >&2; exit 64; }
sha=$(git rev-parse --verify "$commit^{commit}")
branch=ci/$label-${sha:0:8}
git push -q "$remote" "$sha:refs/heads/$branch"
url=$(gh pr list --repo "$fork" --head "$branch" --state open --json url --jq '.[0].url // empty')
if [ -z "$url" ]; then
    url=$(gh pr create --repo "$fork" --base main --head "$branch" --draft \
        --title "test: CI preflight for $label at ${sha:0:8} (do not merge)" \
        --body "Runs the project's own checks, Windows included, on this commit before it is pushed upstream. Not for merging; closed when the checks have finished." | tail -n 1)
fi
number=${url##*/}
echo "scratch draft: $url ($branch)"
deadline=$(( $(date +%s) + minutes * 60 ))
table=
while :; do
    sleep 30
    table=$(gh pr checks "$number" --repo "$fork" 2>/dev/null | awk -F'\t' '{print $2 "\t" $1}' || true)
    if [ -n "$table" ] && ! grep -q -E '^(pending|queued|in_progress)' <<< "$table"; then break; fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
        echo "$table" | sort; echo "hs-fork-ci.sh: timed out after $minutes minutes; the draft is left open: $url" >&2; exit 3
    fi
done
echo "$table" | sort
run_url=$(gh pr checks "$number" --repo "$fork" 2>/dev/null | awk -F'\t' '$1 ~ /Python source checks \(windows/ {print $4; exit}' | sed -E 's#/job/.*##')
[ -n "$run_url" ] && echo "run: $run_url"
windows=$(grep -E $'\tPython source checks \\(windows' <<< "$table" || true)
status=0
if [ -z "$windows" ] || grep -q -v -E '^pass' <<< "$windows"; then status=1
elif grep -q -E '^fail' <<< "$table"; then status=2
fi
if [ "$keep" = 0 ]; then
    gh pr close "$number" --repo "$fork" --delete-branch \
        --comment "Checks finished (exit $status of the preflight script)." > /dev/null
    echo "scratch draft closed, branch deleted"
fi
case $status in
    0) echo "PASS: both Windows parts and every other job" ;;
    1) echo "FAIL: a Windows job did not pass (or none ran)" ;;
    2) echo "READ: Windows passed; another job failed (see above)" ;;
esac
exit $status
