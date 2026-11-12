---
name: hookshot-issue-triage
description: >
  Comb Hookshot's issues and pull requests for what this machine can reproduce, test or answer: refresh the survey,
  score a problem the way the project asks, reproduce it headless, and draft the comment. Use when asked to triage,
  survey, pick work from, or answer reports on NotRexed/Hookshot.
---

# Triage on Hookshot: what this machine can add

**Use for:** issue/PR surveys, reproduction and reply drafts. Read workbench first. Three frequent commands:

```sh
gh issue view <n> --repo NotRexed/Hookshot --comments
gh pr list --repo NotRexed/Hookshot --state all --search "<words>" --json number,title,state,headRefOid
/home/mooki/Games/.hookshot-harness/hs-pr-ci.sh <pr>...
```

Read `reports/POSTED.md` and the **whole** thread, including its latest description, reviews and attachments,
before spending a run or describing a result as a finding. A repeated known result is a repeat, not new work.
Reports name skills read and tools used. Public drafts contain no local paths, machine tool names or agent names.

The historical survey found thirteen `[Tracking]` families (#993, #995 to #1006) and feature requests (#1007);
refresh live state before picking work. `docs/worklist.md` remains the scored list. The new **testing wanted**
label permits non-collaborator PRs in the opt-in test-request queue; claims also live in Matrix/Discord, so
“nobody claims it on GitHub” does not mean unclaimed. This machine adds **Linux with an RTX 4090
on NVIDIA's driver** (Tier 1 for the project) and **an Intel UHD 770 on Mesa** (no mesh shaders: the compute fallback).
Read `skill://hookshot-workbench` for building and running, `skill://hookshot-pr-evidence` before anything is posted.

## The survey and how to refresh it

`reports/HsSurvey.md` and `reports/HsSurvey-files/` are the **2026-11-10 historical survey**. Its `tables.py`
contains manually frozen verdicts and an as-of date: refreshing JSON does not refresh those judgements.
Discover today's `[Tracking]` issues and feature collector from the tracker (newer families include #2502–#2511;
#2595 is a separate benchmark tracker), rather than treating the old thirteen-family list or merge median as timeless.

The existing `fetch.py` writes a shared script-relative `raw/` and has **no `--out` option**. For a concurrent
survey reuse it with a task-local destination, as follows; do not overwrite another lane's snapshot:

```sh
<pinned-python> -I -B - <new-task-raw-directory> labels <<'PY'
import importlib.util, os, subprocess, sys
path = "/home/mooki/Code/hookshot-wt/reports/HsSurvey-files/fetch.py"
spec = importlib.util.spec_from_file_location("survey_fetch", path)
fetch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch)
fetch.RAW = os.path.abspath(sys.argv[1])
os.makedirs(fetch.RAW, exist_ok=False)
sys.argv = [path, *sys.argv[2:]]
try:
    fetch.main()
except subprocess.CalledProcessError as error:
    print(error.stderr or str(error), file=sys.stderr)
    raise
PY
```

For a bounded cohort, use `gh issue list --search "<explicit date/state query>"` and a lean PR index
(`number,title,state,headRefOid`); fetch full bodies/comments/reviews only for selected threads. If the old rich
PR query fails, retain stderr and switch to lean metadata plus paginated REST (`gh api --paginate --slurp
repos/NotRexed/Hookshot/issues/<n>/comments?per_page=100`), not a repeated heavyweight query. Do not diagnose
the unknown failure as a GitHub limit. A result count reaching the requested limit is not a complete inventory:
raise the bound or split disjoint date ranges, deduplicate by number, and verify all comment pages. Distinguish
new openings, updated-only threads and actually new comments. Date your own verdicts; search open **and merged**
PRs again before a claim.

## Scoring (their rule, `docs/workflow.md`)

score = severity weight x reach x confidence. Severity: S0 8 (security; crash, hang, device lost, PC freeze; lost
saves; the builder cannot build), S1 5 (cannot progress or start), S2 3 (wrong but playable; a frame-rate loss over
20 %), S3 1 (polish, features). Reach: the share of players affected (their `scripts/hardware_report.py` counts the
issue forms; Linux and SteamOS were 17 % of reports, NVIDIA 35 %). Confidence: 1.0 reproduced with a log, 0.7 two
reporters with logs, 0.4 one report or no log. Write the three parts in the claim or the comment. A new problem
becomes a row in `docs/worklist.md` through a pull request.

## One report, start to finish

1. Read the whole thread with its logs (`gh issue view <n> --repo NotRexed/Hookshot --comments`; download attached
   logs); read the tracking issue it belongs to; search open and merged pull requests for it.
   A reporter's `source <hex>` is a content fingerprint (`nx2/version.py`), not a Git revision; pin the release tag
   or reported head separately. Read a moved PR's description before a test: “parked/hold off” stops that work.
2. Verdict for this machine before any run: reproducible here (Linux, NVIDIA or the Intel device, the story point
   reachable from a save in `/home/mooki/Games/.hookshot-harness/saves/`), or not (Windows only, AMD, HDR, a real pad).
   “Cannot reproduce this hardware symptom” is separate from “can test its portable API/recording contract with
   authored fixtures here”; a fixture proof must not be promoted into an AMD/Windows gameplay claim.
3. Reproduce headless through the run wrapper at the reporter's settings (their log names version and options); ten
   runs if intermittent; pictures looked at; the log lines that show it. "Does not reproduce here in N runs at these
   settings" is a result worth posting when the reporter's hardware is ours.
4. If someone already has a pull request: build it against its base and test it here; the verdict is one of helps
   (measured), nothing to show here, breaks, cannot be tested here.
5. If nobody has: the cause before the fix; hand a fix to its own branch and pull request.
6. Draft the comment under `/home/mooki/Code/hookshot-wt/reports/drafts/`: what was run (build, settings, driver),
   what was seen, the numbers through a script, what was not tested. The main session posts it.

## What our earlier work answers

`/home/mooki/Code/hookshot-wt/reports/OURS.md` lists everything built for the other port of this game with a verdict
for Hookshot (port as a feature, fix there too, nothing to do). The two ports are unrelated code on the same game
binary: **facts about the game carry over** (its options, flags, files, what it asks of the system; addresses convert:
Hookshot = the other port - 0x7000000000 + 0x80020000), **code faults mostly do not**. A finding from the other port is a
lead until it is reproduced on Hookshot, and is posted as a lead (issue 1460's comment of 2026-11-10 is the model).

## Rules

- Nothing made from the game is posted or attached: no saves, no file lists, no shader dumps. Pictures and
  `hookshot_log.txt` are fine; keep asset names and local paths out of excerpts.
- Do not compete with a claimed fix: offer the reproduction and the evidence, and test their change.
- Everything posted goes into `reports/POSTED.md` with what is owed afterwards.
- Feature descriptions come from the code/thread or an inspected screen, not memory: #2347 is a Bug report row
  that saves a folder, not a tab producing a ZIP.
- Current main's Matrix team updates/test-request queue are opt-in. Read its `AGENTS.md` and the live
  `hookshot-discord-updates` skill; no sign-in, claim or posting action is authorized merely by reading them.
