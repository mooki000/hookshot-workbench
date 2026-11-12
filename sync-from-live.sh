#!/usr/bin/env bash
# Copies the tools and skills from where they live on my machine into this checkout, then runs publish-check.sh.
# It commits nothing and pushes nothing: read `git diff`, then commit.
#
#   ./sync-from-live.sh
#
# What is copied is listed here on purpose, file by file: nothing reaches this repository by a wildcard. To publish a
# new tool, add it to the list, run this, read publish-check.sh's output and the diff.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
H=/home/mooki/Games/.hookshot-harness
S=/home/mooki/.agents/skills

for s in hookshot-workbench hookshot-pr-evidence hookshot-perf-compare hookshot-scripted-play hookshot-issue-triage \
         hookshot-discord-updates hookshot-regression-hunt hookshot-nightly-playtest hookshot-pr-loop; do
    rm -rf "$here/skills/$s"
    cp -r "$S/$s" "$here/skills/$s"
done
mkdir -p "$here/harness" "$here/gates" "$here/play" "$here/docs" "$here/regress" "$here/nightly"
cp "$H/hs-run.sh" "$H/hs-idle.sh" "$H/hs-nocore.sh" "$H/hs-pr-check.sh" "$H/hs-save.sh" "$H/hs-watch-ends2.sh" \
   "$H/hs-watch-threads.py" "$H/hs-window.py" "$H/tools/deps-local.sh" "$here/harness/"
cp "$H/playthrough/hsap.py" "$H/playthrough/hsap_file.py" "$H/playthrough/tapcheck.py" "$H/playthrough/test_hsap.py" \
   "$H/playthrough/test_hsap_file.py" "$here/play/"
cp "$H/regress/regress.py" "$H/regress/build_case.py" "$H/regress/run_case.py" "$H/regress/test_regress.py" "$here/regress/"
for f in hs-nightly.py build.py common.py scenario.py monitor.py metrics.py compare.py store.py report.py hunt.py \
         rolling.py test_compare.py test_driver.py test_rolling.py README.md; do
    cp "$H/nightly/$f" "$here/nightly/$f"
done
cp /home/mooki/Code/hookshot-wt/reports/AGENT-BRIEF.md "$here/docs/agent-brief.md"
find "$here" -name __pycache__ -prune -exec rm -rf {} +

# The machine's gates and a few paths carry names that are only this machine's history. A private step (.private/, not
# in the repository) copies the gates under the names used here and normalises those names in everything copied above.
if [ -x "$here/.private/publish-local.py" ]; then "$here/.private/publish-local.py" "$here"
else echo "sync: .private/publish-local.py is missing: the gates were not copied and names were not normalised" >&2; exit 1
fi

"$here/publish-check.sh"
git -C "$here" status --short
