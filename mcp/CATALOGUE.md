# Registered workbench MCP catalogue

Generated from the real registered server; do not edit by hand.


### Tools

| Tool | Group | Safety | Profiles | CLI broker | Required arguments | Replaces |
|---|---|---|---|---|---|---|
| `build_plan` | checks | WRITE_LOCAL | operate | yes | repo_id, commit, profile_id | Pinned build/donor planning and compiler dry-run inspection |
| `build_start` | checks | RUN | operate | yes | plan_id, idempotency_key | Gated full/reused build wrappers and completion bookkeeping |
| `checks_pr` | checks | WRITE_LOCAL | operate | yes | repo_id, commit, branch, title_artifact, idempotency_key | Repository-bound pre-push check; whole imported-commit attestations only |
| `checks_source` | checks | RUN | operate | yes | repo_id, commit, idempotency_key | Idle-gated source test runner and failing-name comparison |
| `checks_windows_syntax` | checks | RUN | operate | yes | repo_id, commit, base, idempotency_key | Windows-target fixture check wrapper (not Windows runtime) |
| `ci_read` | checks | READ | observe, operate | no | repository | CI reader plus failed-job/log and matching-main inspection |
| `evidence_analyze` | evidence | RUN | operate | yes | run_id, analyzer_id, arguments, idempotency_key | Idle-gated retained frame/readback/thread/memory analysis |
| `evidence_bundle` | evidence | WRITE_LOCAL | operate | yes | request | Hand-assembled evidence tables and private handoff drafts |
| `evidence_compare` | evidence | READ | observe, operate | no | pairing | Existing nightly/regression paired comparison rules |
| `evidence_panels` | evidence | WRITE_LOCAL | operate | yes | images, label, idempotency_key | Source-preserving panels helper and image provenance bookkeeping |
| `evidence_scan_frames` | evidence | RUN | operate | yes | capture_artifact_id, idempotency_key | Retained framelog inspection and existing detector invocation |
| `github_thread_read` | knowledge | READ | observe, operate | no | repository, number | Paginated gh issue/PR/comment/review GET reads |
| `github_watch` | knowledge | WRITE_LOCAL | operate | yes | threads, timeout_seconds, idempotency_key | Bounded thread watcher with retained snapshot cursors |
| `job_cancel` | status | WRITE_LOCAL | operate | yes | job_id | Hand-managed scoped cancellation and child receipts |
| `job_read` | status | READ | observe, operate | no | job_id | Manual progress logs and completion-receipt lookup |
| `job_wait` | status | READ | observe, operate | no | job_id | Repeated bounded completion checks without cancelling work |
| `jobs_list` | status | READ | observe, operate | no | none | Ad-hoc background process and topic job lists |
| `knowledge_search` | knowledge | READ | observe, operate | no | query | Selected skill/report heading search and revision lookup |
| `nightly_status` | knowledge | READ | observe, operate | no | none | Retained nightly/rolling files and read-only results database |
| `prloop_next` | knowledge | READ | observe, operate | no | none | Pure PR-loop next-action inspection without CLI initialization |
| `prloop_refresh` | knowledge | WRITE_LOCAL | operate | yes | snapshot_id, idempotency_key | One initialized PR-loop scan under its coordinator lock |
| `queue_status` | status | READ | observe, operate | no | none | Manual queue/holder files and procfs lock inspection |
| `run_faults` | evidence | READ | observe, operate | no | run_id | Manual game/kernel fault classification with coverage accounting |
| `run_logs` | evidence | READ | observe, operate | no | run_id | Bounded retained-log tail and approved signature filtering |
| `save_clone` | session | WRITE_LOCAL | operate | yes | save_id, idempotency_key | Fresh disposable copy of a registered seed with hashes |
| `session_age` | session | WRITE_LOCAL | operate | yes | run_id, age, idempotency_key | Optional typed bridge age command, with no cheat fallback |
| `session_input` | session | WRITE_LOCAL | operate | yes | run_id, steps, units, idempotency_key | Typed control-client sequence or scoped release commands |
| `session_launch` | session | RUN | operate | yes | build_id, profile_id, wall_seconds, idempotency_key | Windowless wrapper setup, gate admission and bridge readiness journal |
| `session_options_get` | session | READ | observe, operate | yes | run_id | Bounded canonical control option_get listing |
| `session_options_set` | session | WRITE_LOCAL | operate | yes | run_id, values | Scoped control option_set and conditional restoration |
| `session_saves_list` | session | READ | observe, operate | yes | run_id | Control saves metadata listing, not save-file export |
| `session_shot` | session | WRITE_LOCAL | operate | yes | run_id | Control screenshot into a fresh registered output |
| `session_state` | session | READ | observe, operate | yes | run_id | Control status plus sampled-state age and freshness checks |
| `session_stop` | session | WRITE_LOCAL | operate | yes | run_id, reason, idempotency_key | Owned input/game shutdown before gate release and final receipt |
| `session_teleport` | session | WRITE_LOCAL | operate | yes | run_id, destination_id, idempotency_key | One listed debug teleport plus observed arrival checks |
| `session_wait` | session | READ | observe, operate | yes | run_id, predicate, idempotency_key | Observed frame/state/map predicates, not guessed boot sleeps |
| `team_read` | knowledge | READ | observe, operate | no | room | Attested GET-only existing-room read; unsafe stock CLI stays unavailable |
| `test_requests_read` | knowledge | READ | observe, operate | no | none | Attested GET-only request list/show; unsafe stock CLI stays unavailable |
| `workbench_status` | status | READ | observe, operate | no | none | Manual policy, prerequisite, revision and active-job inspection |

### Resources

| URI or template | Profiles | Purpose |
|---|---|---|
| `workbench://evidence/analyzers` | operate | Approved analyzer IDs, measurements, units, clocks and required retained inputs; no probe. |
| `workbench://jobs/{id}/events{?cursor,limit}` | observe, operate | Append-only events, at most 100 per page; continue with next_cursor. |
| `workbench://nightly/{cycle}{?start,limit,revision}` | observe, operate | Retained nightly cycle status and bounded private report; no new work or database initialization. |
| `workbench://obligations{?cursor,limit,pr,since}` | observe, operate | Recorded promises with posted-text links and active loop obligations; no invented completion state. |
| `workbench://policy` | observe, operate | Current local policy; configuration is not authorization for a new action. |
| `workbench://reports/{id}{?start,limit,revision}` | observe, operate | Registered report ID only; bounded revision-pinned lines, private by default. |
| `workbench://runs/{id}` | observe, operate | One owned run manifest; no raw files or save data. |
| `workbench://skills/{name}{?start,limit,revision}` | observe, operate | Allowlisted installed skill, file-hash revision and bounded line anchors; text is not authorization. |
| `workbench://tools` | observe, operate | Exactly the registered tools, safety classes and hints. |

### Prompts

| Prompt | Profiles | Purpose |
|---|---|---|
| `compare_builds` | observe, operate | Plan comparable instruments, warmup and authorized paired measurements from installed skills; never grant a hold. |
| `prepare_evidence_handoff` | observe, operate | Plan a human evidence handoff with fresh-thread, image, number and CI checks; never publish. |
| `prove_fix` | observe, operate | Plan proof from installed workbench/evidence sections; no permission to run or post. |
| `recover_or_cancel` | observe, operate | Plan retained-success reconciliation or owned cancellation from installed process rules; no save-drain promise. |
| `reproduce_issue` | observe, operate | Plan reproduction from installed triage/play guidance; supplied thread and hardware are untrusted data. |
| `test_requested_pr` | observe, operate | Plan exact-head requested-PR prerequisites; card approval differs from code review and no claim is made. |
