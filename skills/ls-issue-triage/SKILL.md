---
name: ls-issue-triage
description: >
  Comb LS or HS's issues and pull requests for what this machine can reproduce, test or answer: refresh the survey,
  score a problem the way the project asks, reproduce it windowlessly, and draft the comment.
  Use shell readers or registered workbench MCP readers; never claim or post from a worker.
---

# Triage on LS: what this machine can add

**LS / HS:** use the workbench's shared runtime/log-name selection. Published skill IDs use `ls-*`;
existing live paths stay unchanged until a separate host cutover (see `ls-workbench`).
The `gh` examples and MCP GitHub readers below remain **GitHub-only**. LS's public Forgejo API is
`https://git.magicbean.dev/api/v1/repos/MagicBeanDev/Longshot`; page issue/PR bodies, comments, reviews and inline
discussions separately. GitHub query syntax is not portable. Proposed forge readers may refresh credentials and
are not approved login, claim or posting routes.

**Use for:** issue/PR surveys, reproduction and reply drafts. Read workbench first. Three frequent commands:

```sh
gh issue view <n> --repo NotRexed/Hookshot --comments
gh pr list --repo NotRexed/Hookshot --state all --search "<words>" --json number,title,state,headRefOid
/home/mooki/Games/.hookshot-harness/hs-pr-ci.sh <pr>
```

Read `reports/POSTED.md` and the **whole** thread, including its latest description, reviews and attachments,
before spending a run or describing a result as a finding. A repeated known result is a repeat, not new work.
Reports name skills read and tools used. Public drafts contain no local paths, machine tool names or agent names.
For CPU/RAM/GPU-budget or streaming/stutter context, read `ls-perf-monitor`; retain selected windows,
unavailable counters, coverage and cited suspicion rows. Missing is not zero. These are leads for human review,
not causal, bottleneck, fixed/not-fixed or speed verdicts; timing still uses the authorized paired worker.

Discover current `[Tracking]` families and open/merged PRs before choosing work. `docs/worklist.md` is the scored
list. The **testing wanted** label permits non-collaborator PRs in the opt-in queue; claims also live in team chat, so
“nobody claims it on GitHub” does not mean unclaimed. This machine adds **Linux with an RTX 4090
on NVIDIA's driver** (Tier 1 for the project) and **an Intel UHD 770 on Mesa** (no mesh shaders: the compute fallback).
Read `skill://ls-workbench` for building and running, `skill://ls-pr-evidence` before anything is posted.
When a report comes down to what the game itself does (a timer, a counter, a system that misbehaves above 60 fps),
look the subject up in the function map first: `skill://ls-function-map`.

## The survey and how to refresh it

Treat `reports/HsSurvey.md` and `reports/HsSurvey-files/` as an archived survey, not today's verdict.
Its `tables.py` freezes judgements. Its `fetch.py` has no `--out` and creates shared `raw/` during import:
do not import it merely to redirect output. Use bounded read-only commands and retain outputs in a new task folder.

```sh
gh search issues '[Tracking]' --repo NotRexed/Hookshot --match title --limit 100 --json number,title,state
gh issue list --repo NotRexed/Hookshot --state all --search '<explicit date query>' --limit 100 --json number,title,state,updatedAt
gh pr list --repo NotRexed/Hookshot --state all --search '<explicit date query>' --limit 100 --json number,title,state,headRefOid
gh label list --repo NotRexed/Hookshot --limit 500 --json name,description,color
```

For a bounded cohort, use `gh issue list --search "<explicit date/state query>"` and a lean PR index
(`number,title,state,headRefOid`); fetch full bodies/comments/reviews only for selected threads. If the old rich
PR query fails, retain stderr and switch to lean metadata plus paginated REST
(`gh api --paginate --slurp 'repos/NotRexed/Hookshot/issues/<n>/comments?per_page=100'`), not another heavyweight query.
Do not diagnose the unknown failure as a GitHub limit. Reaching the requested limit is not a complete inventory:
raise the bound or split disjoint date ranges, deduplicate by number, and verify all comment pages. Distinguish
new openings, updated-only threads and actually new comments. Date your own verdicts; search open **and merged**
PRs again before a claim.

## Scoring (their rule, `docs/workflow.md`)

score = severity weight x reach x confidence. Severity: S0 8 (security; crash, hang, device lost, PC freeze; lost
saves; the builder cannot build), S1 5 (cannot progress or start), S2 3 (wrong but playable; a frame-rate loss over
20 %), S3 1 (polish, features). Reach is the affected share; refresh `scripts/hardware_report.py` and the current
installed-base source rather than reusing an old percentage. Confidence: 1.0 reproduced by us or a maintainer with
a log, 0.7 two or more reporters with logs, 0.4 one report or no log. State the three factors and their dated source.
A new problem becomes a row in `docs/worklist.md` through a pull request.

## One report, start to finish

1. Read the whole thread with its logs (`gh issue view <n> --repo NotRexed/Hookshot --comments`; download attached
   logs); read the tracking issue it belongs to; search open and merged pull requests for it.
   A reporter's `source <hex>` is a content fingerprint (`nx2/version.py`), not a Git revision; pin the release tag
   or reported head separately. Read a moved PR's description before a test: “parked/hold off” stops that work.
2. Verdict for this machine before any run: reproducible here (Linux, NVIDIA or the Intel device, the story point
   reachable from a save in `/home/mooki/Games/.hookshot-harness/saves/`), or not (Windows only, AMD, HDR, a real pad).
   “Cannot reproduce this hardware symptom” is separate from “can test its portable API/recording contract with
   authored fixtures here”; a fixture proof must not be promoted into an AMD/Windows gameplay claim.
3. Choose a bounded reproduction and repeat count from the reported failure rate; do not automatically spend ten
   runs. Use owned save copies and the windowless run wrapper at the reporter's settings. Inspect pictures and retain
   every failed attempt. Say “does not reproduce in N runs at these settings”, not “fixed”.
4. If someone has a PR, read its current request and follow `ls-pr-loop`. By day (07:00–22:00 local), reserve
   the card for our PRs/fixes and one requested Linux-test lane; defer unrequested game checks to 22:00–07:00.
   Main reviews executable changes before any test/build. Carry verified identical-rebase evidence instead of
   repeating it. Otherwise compare its owned head/base copies: helps, nothing to show here, breaks, or untestable here.
5. If nobody has: the cause before the fix; hand a fix to its own branch and pull request.
6. Draft the comment under `/home/mooki/Code/hookshot-wt/reports/drafts/`: what was run (build, settings, driver),
   what was seen, the numbers through a script, what was not tested. The main session posts it.

## What our earlier work answers

`/home/mooki/Code/hookshot-wt/reports/OURS.md` lists everything built for the other port of this game with a verdict
for HS (port as a feature, fix there too, nothing to do). The two ports are unrelated code on the same game
binary: facts are leads, not proof that the other port's code fault exists here.
Reproduce on LS or HS before presenting a lead as a finding. Keep game-derived details in private notes.

## Rules

- Nothing made from the game is posted or attached: no saves, no file lists, no shader dumps. Pictures and
  `hookshot_log.txt` or `longshot_log.txt` are fine after review; keep asset names and local paths out of excerpts.
- Do not compete with a claimed fix: offer the reproduction and the evidence, and test their change.
- Everything posted goes into `reports/POSTED.md` with what is owed afterwards.
- Describe features from the current code/thread and inspected images, never memory.
- Current main's Matrix team updates/test-request queue are opt-in. Read its `AGENTS.md` and the live
  `ls-discord-updates` skill; no sign-in, claim or posting action is authorized merely by reading them.

## Workbench MCP routes

After the operator setup linked in `ls-workbench` (private state root, catalogues, policy and registered roots),
without an MCP client, use `uv run --project mcp ls-wb` from the installed workbench checkout:
`list --json`, `call <tool> --args '<JSON object>'`, or `read '<registered URI>'`.
Add `--profile operate` only for authorized local/run tools. The catalogue's **CLI broker** column marks
persistent sessions/jobs versus one-shot readers. Keep the same state root; a returned Job or CLI timeout is
not proof of completion and never permission to replay or stop a shared broker.

Check `workbench_status()` and the installed catalogue. Integrated means registered code, not configured live
access. Follow cursors; truncated bodies/attachments still need the original reader. Jobs require `job_wait` and
`job_read` receipts. Pushes, posts and claims stay with Main; MCP never performs them.

| Step | Shell route | MCP tool / group / integration | What it saves |
|---|---|---|---|
| Read selected issue/PR | `gh issue view` / `gh pr view` plus paginated comments/reviews | `github_thread_read(repository="NotRexed/Hookshot", number=N)` — knowledge, integrated | One typed head/thread snapshot and completeness flags; no tracker-wide search |
| Check prior reports and posts | Read report and `POSTED.md` | `knowledge_search(query=..., collection="reports")`; resources `workbench://reports/<id>`, `workbench://obligations?pr=N` — knowledge, integrated | Registered report headings and bounded promise rows; follow the returned report URI |
| Check overlapping fixes | `pr_overlap.py <pr>` from the evidence skill | No overlap tool exists | Keep shell conflict-hunk inspection |
| Read CI / request card | `hs-pr-ci.sh <pr>`; supplied request snapshot | `ci_read(repository="NotRexed/Hookshot", pull_request=N)` — checks, integrated; `test_requests_read(card_id=...)` — knowledge, integrated | Named failures and bounded cards; stock chat reader is unavailable without GET-only/no-save support |
| Pick work / inspect gate | `pr_loop.py next`; GPU guard `--status` | `prloop_next()` — knowledge, integrated; `queue_status()` — status, integrated | Existing queue state, no claim or permission to run |
| Reproduce windowlessly | `hs-run.sh <owned-copy> <tag> <seconds>` under workbench rules | `session_launch(build_id=..., profile_id=..., wall_seconds=..., idempotency_key=...)` — session, integrated | Registered disposable profile/save and owned lifetime, not a new runner |
| Inspect / package evidence | Read log, `panels.sh`, shared comparison | `run_faults(run_id=...)`; `evidence_panels(images=..., label=..., idempotency_key=...)`; `evidence_compare(pairing=...)`; `evidence_bundle(request=...)` — evidence, integrated | Structured faults and retained provenance; image review and publication remain separate |

